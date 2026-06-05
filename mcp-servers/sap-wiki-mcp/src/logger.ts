// src/logger.ts
// Winston logger configuration for the SAP Wiki MCP Server
import winston from 'winston';
import { config } from './config.js';
import { existsSync, mkdirSync } from 'fs';

// Ensure log directory exists before creating logger
if (!existsSync(config.log.dir)) {
  mkdirSync(config.log.dir, { recursive: true });
}

/**
 * Winston logger instance configured with multiple transports
 * - File transport for errors only (error.log)
 * - File transport for all log levels (combined.log)
 * - Console transport with colorized output for development
 */
export const logger = winston.createLogger({
  level: config.log.level,
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }), // Include stack traces for errors
    winston.format.json() // JSON format for file logs
  ),
  transports: [
    // Error-only file transport
    new winston.transports.File({
      filename: `${config.log.dir}/error.log`,
      level: 'error',
    }),
    // Combined file transport for all log levels
    new winston.transports.File({
      filename: `${config.log.dir}/combined.log`,
    }),
    // Console transport with colorized output
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.simple()
      ),
    }),
  ],
});
