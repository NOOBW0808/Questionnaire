'use strict';
// Transaction boundaries are returned as data. No session or DB is opened.
const fs = require('node:fs');
const path = require('node:path');
function transactionContracts() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, 'transaction_boundaries.json'), 'utf8'));
}
module.exports = {transactionContracts};
if (require.main === module) process.stdout.write(JSON.stringify(transactionContracts(), null, 2) + '\n');
