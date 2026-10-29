'use strict';
// Intentionally no MongoDB imports or mutation functions.
const fs = require('node:fs');
const path = require('node:path');
function migrationPlan() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, 'migration_plan.json'), 'utf8'));
}
module.exports = {migrationPlan};
if (require.main === module) {
  if (process.argv.slice(2).some(x => x !== '--plan')) throw new Error('Migration execution is not implemented; --plan only.');
  process.stdout.write(JSON.stringify(migrationPlan(), null, 2) + '\n');
}
