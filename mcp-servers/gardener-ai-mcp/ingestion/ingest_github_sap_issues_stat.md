

```
⏺ 全量 ingestion 已完成！汇总如下：

  ══════════════════════════════════════════════════════
    SAP GitHub 全量 ingestion 完成
  ══════════════════════════════════════════════════════
    Repository                    Issues   Pages   Vectors
    kubernetes-canary/issues-canary  8,403   281   141,886
    kubernetes-live/issues-live      9,699   324   250,072
    ─────────────────────────────────────────────────────
    TOTAL                           18,102   605   391,958
  ══════════════════════════════════════════════════════
    运行时间：876 分钟（约 14.6 小时）
    向量维度：1536d (text-embedding-3-small)
    目标集合：gardener_issues

  18,102 个 SAP GitHub issue，共生成 391,958 个向量，全部写入 Qdrant gardener_issues 集合，现在可以对所有 kubernetes-canary 和 kubernetes-live 的历史 issue 进行语义搜索了。
```
