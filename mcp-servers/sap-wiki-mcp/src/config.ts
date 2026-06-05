// src/config.ts
// Configuration management for the SAP Wiki MCP Server
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

// Get the directory of the current module (ES module equivalent of __dirname)
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Project root is one level up from src/
const PROJECT_ROOT = path.resolve(__dirname, '..');

// Load environment variables from .env file in project root
dotenv.config({ path: path.join(PROJECT_ROOT, '.env') });

/**
 * Get absolute path for a directory, with fallback to project-relative path
 */
function getAbsolutePath(envVar: string, defaultRelativePath: string): string {
  const envValue = process.env[envVar];
  
  if (envValue) {
    // If it's already an absolute path, use it
    if (path.isAbsolute(envValue)) {
      return envValue;
    }
    // If it's a relative path, resolve it from project root
    return path.resolve(PROJECT_ROOT, envValue);
  }
  
  // Use default relative to project root
  return path.join(PROJECT_ROOT, defaultRelativePath);
}

/**
 * Parse cache size with unit support (MB, GB)
 * @param envVar - Environment variable name
 * @param defaultValue - Default value with unit (e.g., "100MB")
 * @returns Size in bytes
 */
function parseCacheSize(envVar: string, defaultValue: string): number {
  const value = process.env[envVar] || defaultValue;
  
  // If it's just a number (backward compatibility), treat as MB
  if (/^\d+$/.test(value)) {
    return parseInt(value, 10) * 1024 * 1024; // MB to bytes
  }
  
  // Parse with unit
  const match = value.match(/^(\d+(?:\.\d+)?)\s*(MB|GB)?$/i);
  if (!match) {
    throw new Error(`Invalid cache size format: ${value}. Expected format: "100MB", "2GB", etc.`);
  }
  
  const size = parseFloat(match[1]);
  const unit = (match[2] || 'MB').toUpperCase();
  
  // Special case: 0 means unlimited
  if (size === 0) {
    return 0;
  }
  
  switch (unit) {
    case 'MB':
      return size * 1024 * 1024;
    case 'GB':
      return size * 1024 * 1024 * 1024;
    default:
      throw new Error(`Unsupported cache size unit: ${unit}. Supported units: MB, GB`);
  }
}

/**
 * Application configuration object
 * Contains all settings for the MCP server, Confluence API, caching, and logging
 */
export const config = {
  // MCP Server configuration
  server: {
    name: process.env.MCP_SERVER_NAME || 'sap-wiki-mcp',
    version: process.env.MCP_SERVER_VERSION || '1.0.0',
  },
  
  // Confluence API configuration
  confluence: {
    baseUrl: process.env.CONFLUENCE_BASE_URL || '',
    spaceKeys: process.env.CONFLUENCE_SPACE_KEYS 
      ? process.env.CONFLUENCE_SPACE_KEYS.split(',').map(key => key.trim()).filter(key => key.length > 0)
      : [], // Support both single and multiple spaces with comma separation
    apiToken: process.env.CONFLUENCE_API_TOKEN || '',
    timeout: parseInt(process.env.CONFLUENCE_TIMEOUT || '30000', 10), // 30 seconds default
  },
  
  // Cache configuration
  cache: {
    dir: getAbsolutePath('CACHE_DIR', '.cache'),
    // Optimized cache parameters with flexible unit support
    maxEntries: parseInt(process.env.CACHE_MAX_ENTRIES || '1000', 10), // Max cache entries
    maxSizeBytes: parseCacheSize('CACHE_MAX_SIZE', '100MB'), // Max disk cache size with unit support
  },
  
  // Logging configuration
  log: {
    level: process.env.LOG_LEVEL || 'info',
    dir: getAbsolutePath('LOG_DIR', 'logs'),
  },
  
  // Project root directory (for reference)
  projectRoot: PROJECT_ROOT,
};

/**
 * Get all configured space keys (supports both single and multiple spaces)
 * @returns Array of space keys
 */
export function getAllSpaceKeys(): string[] {
  // First try the new CONFLUENCE_SPACE_KEYS (already parsed)
  if (config.confluence.spaceKeys.length > 0) {
    return config.confluence.spaceKeys;
  }
  
  // Fall back to legacy CONFLUENCE_SPACE_KEY for backward compatibility
  const legacySpaceKey = process.env.CONFLUENCE_SPACE_KEY;
  if (legacySpaceKey) {
    // Also support comma-separated values in the legacy variable
    return legacySpaceKey.split(',').map(key => key.trim()).filter(key => key.length > 0);
  }
  
  return [];
}

/**
 * Validate that all required environment variables are present
 * @throws Error if any required environment variables are missing
 */
export function validateConfig(): void {
  const required = [
    'CONFLUENCE_BASE_URL',
    'CONFLUENCE_API_TOKEN',
  ];

  const missing = required.filter(key => !process.env[key]);

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variables: ${missing.join(', ')}\n` +
      'Please check your .env file or environment configuration.'
    );
  }
  
  // Validate that at least one space is configured
  const spaceKeys = getAllSpaceKeys();
  if (spaceKeys.length === 0) {
    throw new Error(
      'No Confluence spaces configured. Please set CONFLUENCE_SPACE_KEYS in your .env file.\n' +
      'Examples:\n' +
      '  Single space: CONFLUENCE_SPACE_KEYS=DEV\n' +
      '  Multiple spaces: CONFLUENCE_SPACE_KEYS=DEV,TEST,PROD\n' +
      '  Legacy CONFLUENCE_SPACE_KEY is also supported for backward compatibility.'
    );
  }
  
  // Log configuration paths for debugging
  console.log('Configuration loaded:');
  console.log(`  Project root: ${config.projectRoot}`);
  console.log(`  Log directory: ${config.log.dir}`);
  console.log(`  Cache directory: ${config.cache.dir}`);
  console.log(`  Configured spaces: ${spaceKeys.join(', ')}`);
}
