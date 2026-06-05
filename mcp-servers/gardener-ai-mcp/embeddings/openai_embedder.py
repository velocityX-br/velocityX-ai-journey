"""Hyperspace OpenAI-compatible embedding implementation.

Calls ``POST /v1/embeddings`` on the SAP Hyperspace OpenAI-compatible
proxy using the ``openai`` Python SDK with a custom ``base_url``.

Key design decisions:
- Batching: maximum 2048 texts per API call (OpenAI limit).
- Retry: tenacity with exponential back-off on HTTP 429 (rate limit).
- Token awareness: tiktoken warns if a batch exceeds 8000 tokens.
- Dependency injection: the ``openai.AsyncOpenAI`` client is injected
  via the constructor; if omitted the embedder builds one from settings.
"""

from __future__ import annotations

import logging

import openai
import tiktoken
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import Settings
from embeddings.base import BaseEmbedder, EmbeddingError

logger = logging.getLogger(__name__)

# OpenAI enforces a maximum of 2048 inputs per /v1/embeddings request.
_BATCH_SIZE = 2048

# Warn when a single batch exceeds this many tokens (soft limit).
_TOKEN_WARNING_THRESHOLD = 8_000


def _is_rate_limit(exc: BaseException) -> bool:
    """Return True if *exc* represents an HTTP 429 rate-limit response.

    Used as the ``retry`` predicate for tenacity.

    Args:
        exc: The exception raised by the OpenAI SDK.

    Returns:
        ``True`` when the exception is a ``RateLimitError``, ``False``
        otherwise.
    """
    return isinstance(exc, openai.RateLimitError)


class HyperspaceEmbedder(BaseEmbedder):
    """Embed texts via the SAP Hyperspace OpenAI-compatible endpoint.

    The embedder targets ``POST /v1/embeddings`` on the Hyperspace proxy,
    which exposes an OpenAI-compatible API.  The ``openai`` SDK is
    configured with a custom ``base_url`` so that no real OpenAI account
    is required.

    Attributes:
        _settings: The resolved application settings.
        _client: The async OpenAI SDK client pointed at Hyperspace.
        _model: The embedding model identifier (e.g.
            ``"text-embedding-3-small"``).
        _dimensions: The number of vector dimensions produced by the model.
        _tokenizer: A tiktoken encoder used for token-count warnings.
    """

    def __init__(
        self,
        settings: Settings,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        """Initialise the embedder.

        Args:
            settings: Application settings.  Provides ``embedding_model``,
                ``embedding_dimensions``, ``hyperspace_openai_base_url``, and
                ``anthropic_auth_token``.
            client: An optional pre-built ``openai.AsyncOpenAI`` instance.
                When supplied, ``hyperspace_openai_base_url`` and
                ``anthropic_auth_token`` from ``settings`` are ignored.
                Useful for testing with a mock HTTP backend.
        """
        self._settings = settings
        self._model: str = settings.embedding_model
        self._dimensions: int = settings.embedding_dimensions

        if client is None:
            self._client = openai.AsyncOpenAI(
                base_url=settings.hyperspace_openai_base_url,
                api_key=settings.anthropic_auth_token or "placeholder",
            )
        else:
            self._client = client

        # tiktoken: use cl100k_base as a universal approximation for
        # text-embedding-3-* models (same tokeniser family).
        try:
            self._tokenizer = tiktoken.encoding_for_model("text-embedding-3-small")
        except KeyError:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document texts into dense float vectors.

        The input list is split into sub-batches of at most 2048 texts each
        (the OpenAI API limit).  Each sub-batch is sent as a single HTTP
        request.  Results are concatenated and returned in the original order.

        A warning is logged if any sub-batch exceeds
        ``_TOKEN_WARNING_THRESHOLD`` tokens, as very large batches may be
        rejected by the API or produce truncated embeddings.

        Args:
            texts: The list of strings to embed.  Must not be empty.

        Returns:
            A list of float vectors in the same order as ``texts``.  Each
            vector has ``settings.embedding_dimensions`` elements.

        Raises:
            EmbeddingError: If the API returns an unexpected response format.
            openai.APIError: Propagated after all tenacity retries are
                exhausted.
        """
        if not texts:
            return []

        all_vectors: list[list[float]] = []

        for batch_start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[batch_start : batch_start + _BATCH_SIZE]
            self._warn_if_tokens_exceed(batch, batch_start)
            vectors = await self._embed_batch(batch)
            all_vectors.extend(vectors)

        return all_vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string into a dense float vector.

        Delegates to ``embed_documents([text])`` since the Hyperspace
        endpoint makes no distinction between query and document embeddings.

        Args:
            text: The query string to embed.

        Returns:
            A single float vector with ``settings.embedding_dimensions``
            elements.

        Raises:
            EmbeddingError: If ``embed_documents`` returns an empty list.
            openai.APIError: Propagated after all tenacity retries are
                exhausted.
        """
        results = await self.embed_documents([text])
        if not results:
            raise EmbeddingError(
                "embed_documents returned an empty list for a single-text input",
                provider="hyperspace-openai",
            )
        return results[0]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_rate_limit),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
    )
    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Send a single batch to the embeddings endpoint with retry logic.

        The ``@retry`` decorator retries the call up to 5 times with
        exponential back-off (2–60 seconds) whenever the API returns HTTP 429.

        Args:
            batch: A list of up to 2048 strings.

        Returns:
            A list of float vectors, one per input string.

        Raises:
            EmbeddingError: If the response data is malformed.
            openai.RateLimitError: Re-raised after 5 failed attempts.
            openai.APIError: Propagated on non-429 API errors.
        """
        response = await self._client.embeddings.create(
            model=self._model,
            input=batch,
            encoding_format="float",
        )

        if not response.data:
            raise EmbeddingError(
                "Embeddings API returned an empty data list",
                provider="hyperspace-openai",
            )

        # The API guarantees results are ordered by index, but sort
        # defensively to be safe.
        sorted_data = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in sorted_data]

    def _warn_if_tokens_exceed(self, batch: list[str], batch_start: int) -> None:
        """Log a warning if the batch token count exceeds the soft threshold.

        Args:
            batch: The sub-batch of texts about to be sent.
            batch_start: The starting index of this batch in the original
                input list (used for the log message only).
        """
        try:
            total_tokens = sum(
                len(self._tokenizer.encode(text)) for text in batch
            )
            if total_tokens > _TOKEN_WARNING_THRESHOLD:
                logger.warning(
                    "Batch starting at index %d contains %d tokens "
                    "(threshold: %d).  Large batches may be rejected or "
                    "result in truncated embeddings.",
                    batch_start,
                    total_tokens,
                    _TOKEN_WARNING_THRESHOLD,
                )
        except Exception as exc:  # noqa: BLE001
            # Token counting is advisory; never let it block embedding.
            logger.debug("Token counting failed (non-fatal): %s", exc)
