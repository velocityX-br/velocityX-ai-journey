// src/cache.ts
// Cache management system for Confluence pages with ETag-based validation and capacity limits
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, unlinkSync, statSync } from 'fs';
import { join } from 'path';
import { config } from './config.js';
import { logger } from './logger.js';
import { ConfluencePage } from './confluence.js';

interface ETagCacheEntry extends ConfluencePage {
  etag: string;
  cachedAt: string;
  accessCount: number;
  lastAccessAt: string;
}

interface CacheStats {
  memoryEntries: number;
  diskFiles: number;
  totalSizeBytes: number;
  cacheDir: string;
  hitRate: number;
  evictedCount: number;
}

interface HotCacheEntry {
  pageId: string;
  accessCount: number;
  lastAccessAt: string;
  score: number; // Combined hotness score
}

export class CacheManager {
  private memoryCache: Map<string, ETagCacheEntry>;
  private diskCacheDir: string;
  private maxMemoryEntries: number;
  private maxDiskSizeBytes: number;
  private accessStats: Map<string, { hits: number; misses: number }>;
  private evictedCount: number = 0;

  constructor() {
    // Initialize memory cache as Map for better control (no TTL)
    this.memoryCache = new Map();
    this.diskCacheDir = config.cache.dir;
    this.maxMemoryEntries = config.cache.maxEntries;
    
    // Use unit-aware parameter
    this.maxDiskSizeBytes = config.cache.maxSizeBytes;
    
    this.accessStats = new Map();

    // Ensure cache directory exists
    if (!existsSync(this.diskCacheDir)) {
      mkdirSync(this.diskCacheDir, { recursive: true });
      logger.info(`Created cache directory: ${this.diskCacheDir}`);
    }

    // Format size for logging (MB and GB only)
    const sizeDisplay = this.maxDiskSizeBytes === 0 ? 'unlimited' : 
      this.maxDiskSizeBytes >= 1024 * 1024 * 1024 ? `${(this.maxDiskSizeBytes / (1024 * 1024 * 1024)).toFixed(1)}GB` :
      `${(this.maxDiskSizeBytes / (1024 * 1024)).toFixed(1)}MB`;

    logger.info(`ETag-based cache initialized: maxEntries=${this.maxMemoryEntries === 0 ? 'unlimited' : this.maxMemoryEntries}, maxSize=${sizeDisplay}`);
  }

  /**
   * Generate ETag for a Confluence page
   * @param page - The Confluence page
   * @returns ETag string
   */
  private generateETag(page: ConfluencePage): string {
    return `"${page.version}-${page.lastModified}"`;
  }

  /**
   * Calculate hotness score based on access frequency and recency
   */
  private calculateHotness(accessCount: number, lastAccessAt: string): number {
    const now = Date.now();
    const lastAccess = new Date(lastAccessAt).getTime();
    const daysSinceAccess = (now - lastAccess) / (1000 * 60 * 60 * 24);
    
    // Hotness = access frequency / (days since last access + 1)
    // More recent and frequent access = higher score
    return accessCount / (daysSinceAccess + 1);
  }

  /**
   * Update access statistics for a page
   */
  private updateAccessStats(pageId: string, hit: boolean): void {
    if (!this.accessStats.has(pageId)) {
      this.accessStats.set(pageId, { hits: 0, misses: 0 });
    }
    
    const stats = this.accessStats.get(pageId)!;
    if (hit) {
      stats.hits++;
    } else {
      stats.misses++;
    }
  }

  /**
   * Unified intelligent eviction strategy: Improved LRU algorithm based on hotness score
   * Considers both entry count and disk capacity limits
   */
  private evictCacheByHotness(): number {
    const files = readdirSync(this.diskCacheDir)
      .filter(f => f.endsWith('.json'))
      .map(f => {
        const filepath = join(this.diskCacheDir, f);
        const stats = statSync(filepath);
        return { file: f, filepath, size: stats.size, mtime: stats.mtime };
      });

    const totalSize = files.reduce((sum, f) => sum + f.size, 0);
    const totalFiles = files.length;
    
    // Check if eviction is needed (supports unlimited configuration to disable limits)
    const exceedsCount = this.maxMemoryEntries > 0 && totalFiles > this.maxMemoryEntries;
    const exceedsSize = this.maxDiskSizeBytes > 0 && totalSize > this.maxDiskSizeBytes;
    
    if (!exceedsCount && !exceedsSize) {
      return 0; // No eviction needed
    }
    
    logger.debug(`Cache limits check: files=${totalFiles}/${this.maxMemoryEntries}, size=${totalSize}/${this.maxDiskSizeBytes}, needEvict: count=${exceedsCount}, size=${exceedsSize}`);

    // Read hotness information from all cache files
    const entries: (HotCacheEntry & { filepath: string; size: number })[] = [];
    
    for (const file of files) {
      try {
        const data: ETagCacheEntry = JSON.parse(readFileSync(file.filepath, 'utf-8'));
        const pageId = data.id;
        const accessCount = data.accessCount || 1;
        const lastAccessAt = data.lastAccessAt || data.cachedAt;
        const score = this.calculateHotness(accessCount, lastAccessAt);
        
        entries.push({
          pageId,
          accessCount,
          lastAccessAt,
          score,
          filepath: file.filepath,
          size: file.size,
        });
      } catch (error: any) {
        // Corrupted files are directly marked for deletion
        entries.push({
          pageId: file.file,
          accessCount: 0,
          lastAccessAt: '1970-01-01T00:00:00.000Z',
          score: 0,
          filepath: file.filepath,
          size: file.size,
        });
      }
    }

    // Sort by hotness (ascending - coldest first)
    entries.sort((a, b) => a.score - b.score);

    let currentSize = totalSize;
    let currentCount = totalFiles;
    let evicted = 0;

    // Evict coldest files until both limit conditions are satisfied
    for (const entry of entries) {
      // Check if eviction is still needed (supports unlimited configuration)
      const needReduceCount = this.maxMemoryEntries > 0 && currentCount > this.maxMemoryEntries;
      const needReduceSize = this.maxDiskSizeBytes > 0 && currentSize > this.maxDiskSizeBytes;
      
      if (!needReduceCount && !needReduceSize) {
        break; // Limits are now satisfied
      }

      try {
        unlinkSync(entry.filepath);
        currentSize -= entry.size;
        currentCount--;
        evicted++;
        
        // Also remove from memory cache
        this.memoryCache.delete(`page:${entry.pageId}`);
        // Try to find corresponding cache entry by ID to remove title index
        for (const [key, value] of this.memoryCache.entries()) {
          if (key.startsWith('title:') && value.id === entry.pageId) {
            this.memoryCache.delete(key);
            break;
          }
        }
        
        this.evictedCount++;
        logger.debug(`Evicted cold cache entry: ${entry.pageId} (score: ${entry.score.toFixed(3)})`);
        
      } catch (error: any) {
        logger.warn(`Failed to evict cache file: ${entry.filepath}`, { error: error.message });
      }
    }

    if (evicted > 0) {
      const freedSpace = totalSize - currentSize;
      logger.info(`Evicted ${evicted} cold cache entries, freed ${freedSpace} bytes, remaining: ${currentCount} files`);
    }

    return evicted;
  }

  /**
   * Save a page to both memory and disk cache with ETag
   * @param page - The Confluence page to cache
   */
  savePage(page: ConfluencePage): void {
    const etag = this.generateETag(page);
    const now = new Date().toISOString();
    
    const cacheEntry: ETagCacheEntry = {
      ...page,
      etag,
      cachedAt: now,
      accessCount: 1,
      lastAccessAt: now,
    };

    // Update existing entry's access count if it exists
    const existingEntry = this.memoryCache.get(`page:${page.id}`);
    if (existingEntry) {
      cacheEntry.accessCount = existingEntry.accessCount + 1;
    }

    // Save to memory cache
    this.memoryCache.set(`page:${page.id}`, cacheEntry);
    this.memoryCache.set(`title:${page.title}`, cacheEntry);

    // Save to disk cache
    const filename = this.getFilename(page.id);
    const filepath = join(this.diskCacheDir, filename);
    
    writeFileSync(filepath, JSON.stringify(cacheEntry, null, 2));
    
    // Check and evict if over limits
    this.evictCacheByHotness();

    logger.debug(`Cached page with ETag: ${page.title} (${page.id}) ETag: ${etag}, access count: ${cacheEntry.accessCount}`);
  }

  /**
   * Retrieve a page from cache by page ID (optimized with access tracking)
   * @param pageId - The Confluence page ID
   * @returns The cached page or null if not found
   */
  getPage(pageId: string): ConfluencePage | null {
    // Check memory cache first
    const cached = this.memoryCache.get(`page:${pageId}`);
    if (cached) {
      // Update access statistics
      cached.accessCount++;
      cached.lastAccessAt = new Date().toISOString();
      this.updateAccessStats(pageId, true);
      
      logger.debug(`Cache hit (memory): ${pageId}, access count: ${cached.accessCount}`);
      return cached;
    }

    // Check disk cache
    const filename = this.getFilename(pageId);
    const filepath = join(this.diskCacheDir, filename);

    if (existsSync(filepath)) {
      try {
        const data: ETagCacheEntry = JSON.parse(readFileSync(filepath, 'utf-8'));
        
        // Update access info
        data.accessCount = (data.accessCount || 0) + 1;
        data.lastAccessAt = new Date().toISOString();
        
        // Move to memory cache (hot data promotion)
        this.memoryCache.set(`page:${pageId}`, data);
        this.memoryCache.set(`title:${data.title}`, data);
        
        // Check capacity after promotion
        this.evictCacheByHotness();
        
        this.updateAccessStats(pageId, true);
        logger.debug(`Cache hit (disk->memory): ${pageId}, access count: ${data.accessCount}`);
        return data;
      } catch (error: any) {
        logger.warn(`Corrupt cache file: ${filepath}`, { error: error.message });
        unlinkSync(filepath);
      }
    }

    this.updateAccessStats(pageId, false);
    logger.debug(`Cache miss: ${pageId}`);
    return null;
  }

  /**
   * Retrieve a page from cache by title
   * @param title - The page title to search for
   * @returns The cached page or null if not found
   */
  getPageByTitle(title: string): ConfluencePage | null {
    // Check memory cache first
    const cached = this.memoryCache.get(`title:${title}`);
    if (cached) {
      cached.accessCount++;
      cached.lastAccessAt = new Date().toISOString();
      return cached;
    }

    // Search through disk cache files
    const files = readdirSync(this.diskCacheDir);
    for (const file of files) {
      if (file.endsWith('.json')) {
        const filepath = join(this.diskCacheDir, file);
        try {
          const data: ETagCacheEntry = JSON.parse(readFileSync(filepath, 'utf-8'));
          if (data.title === title) {
            // Update access info and promote to memory
            data.accessCount = (data.accessCount || 0) + 1;
            data.lastAccessAt = new Date().toISOString();
            this.memoryCache.set(`title:${title}`, data);
            this.memoryCache.set(`page:${data.id}`, data);
            this.evictCacheByHotness();
            return data;
          }
        } catch (error: any) {
          logger.warn(`Error reading cache file: ${file}`, { error: error.message });
        }
      }
    }

    return null;
  }

  /**
   * Get all cached pages
   * @returns Array of all cached pages
   */
  listPages(): ConfluencePage[] {
    const files = readdirSync(this.diskCacheDir);
    return files
      .filter(f => f.endsWith('.json'))
      .map(file => {
        const filepath = join(this.diskCacheDir, file);
        try {
          return JSON.parse(readFileSync(filepath, 'utf-8'));
        } catch {
          return null;
        }
      })
      .filter(page => page !== null);
  }

  /**
   * Search cached pages by title or content
   * @param query - Search query string
   * @returns Array of matching pages
   */
  search(query: string): ConfluencePage[] {
    const pages = this.listPages();
    const lowerQuery = query.toLowerCase();

    return pages.filter(page => 
      page.title.toLowerCase().includes(lowerQuery) ||
      page.content.toLowerCase().includes(lowerQuery)
    );
  }

  /**
   * Clear all cached data from memory and disk
   */
  clear(): void {
    this.memoryCache.clear();
    this.accessStats.clear();
    this.evictedCount = 0;
    
    const files = readdirSync(this.diskCacheDir);
    files.forEach(file => {
      const filepath = join(this.diskCacheDir, file);
      if (file.endsWith('.json')) {
        unlinkSync(filepath);
        logger.debug(`Cleared cache: ${file}`);
      }
    });
  }

  /**
   * Generate filename for cached page
   * @param pageId - The page ID
   * @returns Filename string
   */
  private getFilename(pageId: string): string {
    return `page_${pageId}.json`;
  }

  /**
   * Get comprehensive cache statistics
   * @returns Object containing cache statistics
   */
  getStats(): CacheStats {
    const diskFiles = readdirSync(this.diskCacheDir).filter(f => f.endsWith('.json'));
    const totalSize = diskFiles.reduce((sum, file) => {
      const filepath = join(this.diskCacheDir, file);
      return sum + statSync(filepath).size;
    }, 0);

    const totalHits = Array.from(this.accessStats.values()).reduce((sum, stats) => sum + stats.hits, 0);
    const totalMisses = Array.from(this.accessStats.values()).reduce((sum, stats) => sum + stats.misses, 0);
    const hitRate = totalHits + totalMisses > 0 ? totalHits / (totalHits + totalMisses) : 0;

    return {
      memoryEntries: this.memoryCache.size,
      diskFiles: diskFiles.length,
      totalSizeBytes: totalSize,
      cacheDir: this.diskCacheDir,
      hitRate: Math.round(hitRate * 100) / 100,
      evictedCount: this.evictedCount,
    };
  }

  /**
   * Get hottest (most accessed) pages
   */
  getHottestPages(limit: number = 10): HotCacheEntry[] {
    const entries: HotCacheEntry[] = [];
    
    for (const [key, entry] of this.memoryCache.entries()) {
      if (key.startsWith('page:')) {
        const pageId = key.substring(5);
        const score = this.calculateHotness(entry.accessCount, entry.lastAccessAt);
        entries.push({
          pageId,
          accessCount: entry.accessCount,
          lastAccessAt: entry.lastAccessAt,
          score,
        });
      }
    }

    return entries
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  /**
   * Clean up cache based on capacity limits (replaces TTL-based cleanup)
   * @returns Number of items cleaned up
   */
  cleanExpired(): number {
    // Use unified eviction strategy for all cleanup
    const cleanedCount = this.evictCacheByHotness();
    
    // Also clean corrupted files
    let corruptedCount = 0;
    const files = readdirSync(this.diskCacheDir);
    files.forEach(file => {
      if (file.endsWith('.json')) {
        const filepath = join(this.diskCacheDir, file);
        try {
          JSON.parse(readFileSync(filepath, 'utf-8'));
        } catch (error: any) {
          logger.error(`Removing corrupted cache file ${file}:`, { error: error.message });
          try {
            unlinkSync(filepath);
            corruptedCount++;
          } catch (removeError: any) {
            logger.error(`Failed to remove corrupted file: ${filepath}`, { error: removeError.message });
          }
        }
      }
    });

    const totalCleaned = cleanedCount + corruptedCount;
    if (totalCleaned > 0) {
      logger.info(`Cleaned up ${totalCleaned} cache entries/files (${cleanedCount} by hotness, ${corruptedCount} corrupted)`);
    }

    return totalCleaned;
  }


  /**
   * Check if cached page is still valid using ONLY ETag (no TTL)
   * @param pageId - The page ID to check
   * @param currentPage - Current page data from API (optional, for comparison)
   * @returns True if cache is valid, false otherwise
   */
  isValidETag(pageId: string, currentPage?: ConfluencePage): boolean {
    const filename = this.getFilename(pageId);
    const filepath = join(this.diskCacheDir, filename);
    
    if (!existsSync(filepath)) {
      return false; // No cache file exists
    }

    try {
      const cachedData: ETagCacheEntry = JSON.parse(readFileSync(filepath, 'utf-8'));
      
      if (!cachedData.etag) {
        return false; // Old cache format without ETag
      }

      if (currentPage) {
        const currentETag = this.generateETag(currentPage);
        const isValid = cachedData.etag === currentETag;
        
        logger.debug(`ETag validation: ${pageId} cached:(${cachedData.etag}) current:(${currentETag}) valid:${isValid}`);
        return isValid;
      }

      // No current page provided, assume valid (ETag-only approach)
      return true;
      
    } catch (error: any) {
      logger.error(`Error validating ETag for ${pageId}:`, { error: error.message });
      return false;
    }
  }

  /**
   * Smart page retrieval with ETag validation
   * @param pageId - The page ID to retrieve
   * @param confluenceClient - Optional Confluence client for validation
   * @returns The page data or null if not available
   */
  async getPageSmart(pageId: string, confluenceClient?: any): Promise<ConfluencePage | null> {
    // First check if we have any cached data
    const cachedPage = this.getPage(pageId);
    
    if (!cachedPage) {
      logger.debug(`No cache for page ${pageId}, requires full fetch`);
      return null; // No cache, caller should fetch from API
    }

    // If no client provided, return cached data
    if (!confluenceClient) {
      return cachedPage;
    }

    try {
      // Get basic page info from API for ETag comparison
      const apiPageInfo = await confluenceClient.getPage(pageId);
      
      if (this.isValidETag(pageId, apiPageInfo)) {
        logger.debug(`ETag valid, using cached content: ${pageId}`);
        return cachedPage; // ETag matches, use cached content
      } else {
        logger.debug(`ETag invalid, cache outdated: ${pageId}`);
        // Cache is outdated, save the new data and return it
        this.savePage(apiPageInfo);
        return apiPageInfo;
      }
      
    } catch (error: any) {
      logger.warn(`Failed to validate ETag for ${pageId}, using cached data:`, { error: error.message });
      return cachedPage; // API failed, use cached data as fallback
    }
  }

  /**
   * Refresh a specific page cache if ETag indicates it's outdated
   * @param pageId - The page ID to refresh
   * @param confluenceClient - Confluence client for fetching fresh data
   * @returns Updated page data or cached data if refresh failed
   */
  async refreshPageCache(pageId: string, confluenceClient: any): Promise<ConfluencePage> {
    try {
      const freshPage = await confluenceClient.getPage(pageId);
      
      // Check if content actually changed before updating cache
      if (!this.isValidETag(pageId, freshPage)) {
        this.savePage(freshPage);
        logger.info(`Refreshed cache for page: ${pageId}`);
      } else {
        logger.debug(`Page unchanged, cache not updated: ${pageId}`);
      }
      
      return freshPage;
      
    } catch (error: any) {
      logger.error(`Failed to refresh cache for ${pageId}:`, { error: error.message });
      
      // Fall back to cached data if available
      const cachedPage = this.getPage(pageId);
      if (cachedPage) {
        logger.warn(`Using stale cache data for ${pageId} due to refresh failure`);
        return cachedPage;
      }
      
      throw error; // Re-throw if no cached data available
    }
  }
}
