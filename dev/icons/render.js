// Render SVG -> PNG cho icon ribbon T3Lab.
//
// Khong goi truc tiep: `dev/build_icons.py` goi script nay voi mot file JSON
// chua danh sach job [{ svg, png, size }].
//
// Dung @resvg/resvg-js vi no deterministic - cung mot SVG luon ra cung mot
// pixel, nen `audit_icons.py` co the so PNG voi SVG de biet build da chay chua.

const fs = require('fs');
const { Resvg } = require('@resvg/resvg-js');

const jobsFile = process.argv[2];
if (!jobsFile) {
  console.error('usage: node render.js <jobs.json>');
  process.exit(2);
}

const jobs = JSON.parse(fs.readFileSync(jobsFile, 'utf8'));
let done = 0;
const failures = [];

for (const job of jobs) {
  try {
    const svg = fs.readFileSync(job.svg, 'utf8');
    const resvg = new Resvg(svg, {
      fitTo: { mode: 'width', value: job.size },
      background: 'rgba(0,0,0,0)',
    });
    fs.writeFileSync(job.png, resvg.render().asPng());
    done += 1;
  } catch (err) {
    failures.push({ svg: job.svg, error: String(err && err.message || err) });
  }
}

console.log(JSON.stringify({ rendered: done, failures }));
process.exit(failures.length ? 1 : 0);
