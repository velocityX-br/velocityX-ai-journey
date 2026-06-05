// src/markdown-converter.ts
// Converts Confluence HTML content to clean Markdown format for LLM consumption
import TurndownService from 'turndown';
import { logger } from './logger.js';

/**
 * Markdown converter class for transforming Confluence HTML to Markdown
 */
export class MarkdownConverter {
  private turndownService: TurndownService;

  constructor() {
    // Initialize Turndown service with optimal settings for LLM consumption
    this.turndownService = new TurndownService({
      headingStyle: 'atx',  // Use # style headings
      hr: '---',            // Horizontal rule style
      bulletListMarker: '-', // Use - for bullet lists
      codeBlockStyle: 'fenced', // Use ``` for code blocks
      fence: '```',         // Code fence marker
      emDelimiter: '*',     // Use * for emphasis
      strongDelimiter: '**', // Use ** for strong
      linkStyle: 'inlined', // Use inline links
      linkReferenceStyle: 'full', // Full reference links
    });

    this.setupConfluenceRules();
  }

  /**
   * Convert Confluence HTML content to clean Markdown
   * @param confluenceHtml - Raw Confluence storage format HTML
   * @returns Clean Markdown suitable for LLM processing
   */
  convertToMarkdown(confluenceHtml: string): string {
    try {
      logger.debug('Converting Confluence HTML to Markdown');
      
      // Pre-process Confluence-specific content
      let processedHtml = this.preprocessConfluenceContent(confluenceHtml);
      
      // Convert to Markdown using Turndown
      let markdown = this.turndownService.turndown(processedHtml);
      
      // Post-process the Markdown for better LLM consumption
      markdown = this.postprocessMarkdown(markdown);
      
      logger.debug('Successfully converted HTML to Markdown');
      return markdown;
      
    } catch (error: any) {
      logger.error('Failed to convert HTML to Markdown:', { error: error.message });
      // Fallback: return cleaned HTML if conversion fails
      return this.fallbackCleanHtml(confluenceHtml);
    }
  }

  /**
   * Set up custom rules for Confluence-specific elements
   */
  private setupConfluenceRules(): void {
    // Handle Confluence layout sections
    this.turndownService.addRule('confluenceLayout', {
      filter: ['ac:layout', 'ac:layout-section', 'ac:layout-cell'] as any,
      replacement: (content) => {
        // Simply return the content, removing the layout wrapper
        return content + '\n\n';
      }
    });

    // Handle Confluence structured macros (panels, info boxes, code blocks, etc.)
    this.turndownService.addRule('confluenceMacros', {
      filter: (node) => {
        return node.nodeName === 'AC:STRUCTURED-MACRO';
      },
      replacement: (content, node: any) => {
        const macroName = node.getAttribute('ac:name');
        
        switch (macroName) {
          case 'code':
            // Handle code blocks with proper formatting
            const language = this.getParameterValue(node, 'language') || '';
            const title = this.getParameterValue(node, 'title');
            const linenumbers = this.getParameterValue(node, 'linenumbers');
            const collapse = this.getParameterValue(node, 'collapse');
            
            // Extract the actual code content from ac:plain-text-body or content
            const codeContent = this.extractCodeContent(node) || content;
            
            let result = '\n';
            
            // Add metadata if present
            if (title || linenumbers === 'true' || collapse === 'true') {
              result += '**code:**';
              if (language) result += ` ${language}`;
              if (title) result += ` ${title}`;
              if (linenumbers === 'true') result += ' linenumbers';
              if (collapse === 'true') result += ' collapse';
              result += '\n';
            }
            
            result += '```';
            if (language && language !== 'none') result += language;
            result += '\n' + codeContent.trim() + '\n```\n\n';
            
            return result;
            
          case 'panel':
            const panelTitle = this.getParameterValue(node, 'title');
            let panelContent = `\n> **Panel:**`;
            if (panelTitle) panelContent += `\n> ${panelTitle}`;
            panelContent += `\n> ${content.replace(/\n/g, '\n> ')}\n\n`;
            return panelContent;
            
          case 'info':
            return `\n> ℹ️ **Info:**\n> ${content.replace(/\n/g, '\n> ')}\n\n`;
          case 'warning':
            return `\n> ⚠️ **Warning:**\n> ${content.replace(/\n/g, '\n> ')}\n\n`;
          case 'note':
            return `\n> 📝 **Note:**\n> ${content.replace(/\n/g, '\n> ')}\n\n`;
          case 'toc':
            return `\n**Table of Contents**\n_(Auto-generated TOC would appear here)_\n\n`;
          case 'anchor':
            const anchorName = this.getParameterValue(node, '') || '';
            return anchorName ? `<a name="${anchorName}"></a>` : '';
          case 'page-info':
            return `_(Page info macro)_`;
          default:
            return content ? `\n**${macroName}:** ${content}\n\n` : '';
        }
      }
    });

    // Handle Confluence rich text body
    this.turndownService.addRule('confluenceRichTextBody', {
      filter: 'ac:rich-text-body' as any,
      replacement: (content) => content
    });

    // Handle Confluence parameters
    this.turndownService.addRule('confluenceParameters', {
      filter: 'ac:parameter' as any,
      replacement: (content) => content
    });

    // Handle Confluence links
    this.turndownService.addRule('confluenceLinks', {
      filter: (node) => {
        return node.nodeName === 'AC:LINK' || node.nodeName === 'RI:USER';
      },
      replacement: (content, node: any) => {
        if (node.nodeName === 'RI:USER') {
          const userKey = node.getAttribute('ri:userkey');
          return `@user(${userKey})`;
        }
        return content || '[Link]';
      }
    });

    // Handle Confluence images
    this.turndownService.addRule('confluenceImages', {
      filter: (node) => {
        return node.nodeName === 'AC:IMAGE' || node.nodeName === 'RI:ATTACHMENT';
      },
      replacement: (content, node: any) => {
        if (node.nodeName === 'RI:ATTACHMENT') {
          const filename = node.getAttribute('ri:filename');
          return filename ? `![${filename}](attachment:${filename})` : '![Image]';
        }
        return '![Image]';
      }
    });

    // Handle auto-cursor-target paragraphs (remove them as they're Confluence editor artifacts)
    this.turndownService.addRule('autoCursorTarget', {
      filter: (node: any) => {
        return node.nodeName === 'P' && 
               node.className && 
               node.className.includes('auto-cursor-target');
      },
      replacement: () => '' // Remove these empty paragraphs
    });

    // Handle Confluence plain text bodies (used in code macros)
    this.turndownService.addRule('confluencePlainTextBody', {
      filter: 'ac:plain-text-body' as any,
      replacement: (content) => content
    });
  }

  /**
   * Helper method to extract parameter values from Confluence macro nodes
   */
  private getParameterValue(node: any, paramName: string): string | null {
    const param = node.querySelector(`ac\\:parameter[ac\\:name="${paramName}"]`);
    return param ? param.textContent?.trim() || null : null;
  }

  /**
   * Helper method to extract code content from Confluence code macros
   */
  private extractCodeContent(node: any): string | null {
    // Try to find ac:plain-text-body first (preferred for code blocks)
    const plainTextBody = node.querySelector('ac\\:plain-text-body');
    if (plainTextBody) {
      return plainTextBody.textContent || '';
    }
    
    // Fallback to rich-text-body
    const richTextBody = node.querySelector('ac\\:rich-text-body');
    if (richTextBody) {
      return richTextBody.textContent || '';
    }
    
    // Last resort: get all text content
    return node.textContent || '';
  }

  /**
   * Pre-process Confluence content before Markdown conversion
   */
  private preprocessConfluenceContent(html: string): string {
    // Remove XML namespace declarations that might interfere with parsing
    html = html.replace(/\s*xmlns[^=]*="[^"]*"/g, '');
    
    // Convert self-closing tags to properly closed tags for better parsing
    html = html.replace(/<(ac:[^>]+?)\/>/g, '<$1></$1>');
    
    // Handle line breaks in Confluence format
    html = html.replace(/<br\s*\/?>/g, '\n');
    
    // Preserve content within ac:plain-text-body tags (important for code blocks)
    html = html.replace(/<ac:plain-text-body><!\[CDATA\[(.*?)\]\]><\/ac:plain-text-body>/gs, 
      '<ac:plain-text-body>$1</ac:plain-text-body>');
    
    // Clean up excessive whitespace but preserve structure in code blocks
    html = html.replace(/\s+(?![^<]*<\/ac:plain-text-body>)/g, ' ');
    
    return html;
  }

  /**
   * Post-process Markdown to improve LLM readability
   */
  private postprocessMarkdown(markdown: string): string {
    // Remove excessive line breaks (more than 2 consecutive) but preserve code block formatting
    markdown = markdown.replace(/\n{3,}(?!```)/g, '\n\n');
    
    // Clean up whitespace around headings
    markdown = markdown.replace(/\n+#/g, '\n\n#');
    
    // Ensure proper spacing around lists
    markdown = markdown.replace(/\n-\s/g, '\n\n- ');
    
    // Clean up empty blockquotes
    markdown = markdown.replace(/>\s*\n>/g, '>\n>');
    
    // Fix escaped backticks in inline text that should be code blocks
    // Handle "Example: ``` { content } ```" patterns - more comprehensive approach
    markdown = markdown.replace(/Example:\s*```\s*(\{[^}]*\})\s*```/g, 'Example:\n```\n$1\n```');
    markdown = markdown.replace(/Example:\s*\\`\\`\\`\s*(\{[^}]*\})\s*\\`\\`\\`/g, 'Example:\n```\n$1\n```');
    
    // Fix general escaped backtick patterns
    markdown = markdown.replace(/\\`\\`\\`\s*(\{[^}]*\})\s*\\`\\`\\`/g, '\n```\n$1\n```\n');
    markdown = markdown.replace(/```\s*(\{[^`]*?\})\s*```/g, '\n```\n$1\n```\n');
    
    // Fix patterns with array content like [job1, job2]
    markdown = markdown.replace(/```\s*(\{[^}]*\[[^\]]*\][^}]*\})\s*```/g, '\n```\n$1\n```\n');
    markdown = markdown.replace(/\\`\\`\\`\s*(\{[^}]*\[[^\]]*\][^}]*\})\s*\\`\\`\\`/g, '\n```\n$1\n```\n');
    
    // Fix escaped double asterisks that should be bold
    markdown = markdown.replace(/\\\*\\\*([^*]+?)\\\*\\\*/g, '**$1**');
    
    // Fix patterns where backticks appear in the middle of text
    markdown = markdown.replace(/(\w+):\s*```\s*(\{[^}]*\})\s*```/g, '$1:\n```\n$2\n```');
    markdown = markdown.replace(/(\w+):\s*\\`\\`\\`\s*(\{[^}]*\})\s*\\`\\`\\`/g, '$1:\n```\n$2\n```');
    
    // Fix code block formatting issues
    markdown = markdown.replace(/```(\w+)?\n\n+/g, '```$1\n');
    markdown = markdown.replace(/\n\n+```/g, '\n```');
    
    // Ensure proper spacing around code blocks
    markdown = markdown.replace(/(\n```[\s\S]*?```)\n{1}([^\n])/g, '$1\n\n$2');
    
    // Remove trailing whitespace from lines
    markdown = markdown.replace(/[ \t]+$/gm, '');
    
    // Ensure file ends with single newline
    markdown = markdown.trim() + '\n';
    
    return markdown;
  }

  /**
   * Fallback method when Markdown conversion fails
   * Returns cleaned HTML with basic formatting removed
   */
  private fallbackCleanHtml(html: string): string {
    logger.warn('Using fallback HTML cleaning method');
    
    // Remove Confluence-specific tags
    let cleaned = html.replace(/<\/?ac:[^>]*>/g, '');
    cleaned = cleaned.replace(/<\/?ri:[^>]*>/g, '');
    
    // Remove style attributes
    cleaned = cleaned.replace(/\s*style="[^"]*"/g, '');
    cleaned = cleaned.replace(/\s*class="[^"]*"/g, '');
    
    // Convert basic HTML tags to text equivalents
    cleaned = cleaned.replace(/<h([1-6])>/g, (match, level) => '\n' + '#'.repeat(parseInt(level)) + ' ');
    cleaned = cleaned.replace(/<\/h[1-6]>/g, '\n\n');
    cleaned = cleaned.replace(/<p>/g, '\n');
    cleaned = cleaned.replace(/<\/p>/g, '\n');
    cleaned = cleaned.replace(/<br\s*\/?>/g, '\n');
    cleaned = cleaned.replace(/<strong>/g, '**');
    cleaned = cleaned.replace(/<\/strong>/g, '**');
    cleaned = cleaned.replace(/<em>/g, '*');
    cleaned = cleaned.replace(/<\/em>/g, '*');
    
    // Remove remaining HTML tags
    cleaned = cleaned.replace(/<[^>]*>/g, '');
    
    // Clean up whitespace
    cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
    cleaned = cleaned.trim();
    
    return cleaned;
  }
}

// Export singleton instance
export const markdownConverter = new MarkdownConverter();
