'use strict';
// Review only: never connects to MongoDB and never executes DDL.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const raw = fs.readFileSync(path.join(__dirname, 'schemas.json'));
const spec = JSON.parse(raw);
const baseline = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'schemas.json'), 'utf8'));
const added = Object.keys(spec.collections).filter(k => !baseline.collections[k]);
process.stdout.write(JSON.stringify({mode: 'REVIEW_ONLY', sha256: crypto.createHash('sha256').update(raw).digest('hex'),
  addedCollections: added, expandedValidators: ['surveys', 'survey_versions'],
  indexes: Object.fromEntries(Object.entries(spec.collections).map(([n,c]) => [n,c.indexes])),
  executor: 'scripts/phase2_schema.py; explicit maintenance/backup/verify/activate required'}, null, 2) + '\n');
