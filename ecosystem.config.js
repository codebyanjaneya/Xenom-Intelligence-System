/**
 * PM2 Ecosystem Configuration for XENOM INTELLIGENCE
 *
 * Run with: pm2 start ecosystem.config.js
 *
 * Commands:
 *   pm2 start ecosystem.config.js          - Start all processes
 *   pm2 stop ecosystem.config.js           - Stop all processes
 *   pm2 restart ecosystem.config.js        - Restart all processes
 *   pm2 delete ecosystem.config.js         - Delete from PM2 list
 *   pm2 logs                               - View all logs
 *   pm2 monit                              - Monitor processes
 */

const path = require('path');

module.exports = {
  apps: [
    {
      // XENOM INTELLIGENCE Backend API + Background Scheduler
      name: 'xenom-backend',
      script: path.join(__dirname, 'backend', '.venv', 'Scripts', 'python.exe'),
      args: '-m uvicorn app.main:app --host 0.0.0.0 --port 8000',
      cwd: path.join(__dirname, 'backend'),
      interpreter: 'none',
      env: {
        NODE_ENV: 'production',
        PYTHONUNBUFFERED: '1',
        ENVIRONMENT: process.env.ENVIRONMENT || 'development',
        DATABASE_URL: process.env.DATABASE_URL,
        JWT_SECRET: process.env.JWT_SECRET,
        LOG_LEVEL: process.env.LOG_LEVEL || 'info',
        SCHEDULER_ENABLED: process.env.SCHEDULER_ENABLED || 'true',
        SCHEDULER_INTERVAL_MINUTES: process.env.SCHEDULER_INTERVAL_MINUTES || '10',
      },
      merge_logs: true,
      error_file: 'logs/backend-error.log',
      out_file: 'logs/backend-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      max_memory_restart: '500M',
      watch: false,
      ignore_watch: ['node_modules', 'logs', '.git'],
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
    },
    {
      // XENOM INTELLIGENCE Frontend Development Server
      name: 'xenom-frontend',
      script: 'cmd',
      args: '/c npm run dev',
      cwd: path.join(__dirname, 'frontend'),
      env: {
        NODE_ENV: 'development',
        VITE_API_URL: process.env.VITE_API_URL || '/api',
      },
      merge_logs: true,
      error_file: 'logs/frontend-error.log',
      out_file: 'logs/frontend-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
    },
  ],

  // Cluster mode disabled for development; set to 'cluster' for production scaling
  // deploy: {
  //   production: {
  //     user: 'deploy',
  //     host: 'your-production-server.com',
  //     key: '~/.ssh/id_rsa',
  //     ref: 'origin/main',
  //     repo: 'git@github.com:your-org/xenom-intelligence.git',
  //     path: '/var/www/xenom-intelligence',
  //     'post-deploy': 'npm install && pm2 restart ecosystem.config.js --env production'
  //   }
  // }
};
