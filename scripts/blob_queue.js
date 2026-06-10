#!/usr/bin/env node
// Mini-side helper for the guestbook Blob queue, using the official
// @vercel/blob SDK (list/del have no stable documented REST shape — the SDK
// is the supported surface). The Python reviewer shells out to this.
//
// Usage:
//   node scripts/blob_queue.js list <prefix>        → JSON [{url, pathname, uploadedAt}]
//   node scripts/blob_queue.js del <url> [<url>...] → deletes, prints count
//
// Needs BLOB_READ_WRITE_TOKEN in env (Mini .env has it). `npm install` on the
// Mini provides @vercel/blob (package.json dependency; Vercel deploys skip
// install entirely via vercel.json, so this dep never ships to the site).

const main = async () => {
  const [, , cmd, ...args] = process.argv;
  const { list, del } = await import("@vercel/blob");

  if (cmd === "list") {
    const prefix = args[0] || "";
    const out = [];
    let cursor;
    do {
      const page = await list({ prefix, cursor, limit: 1000 });
      for (const b of page.blobs) {
        out.push({ url: b.url, pathname: b.pathname, uploadedAt: b.uploadedAt });
      }
      cursor = page.hasMore ? page.cursor : undefined;
    } while (cursor);
    process.stdout.write(JSON.stringify(out));
    return;
  }

  if (cmd === "del") {
    if (!args.length) throw new Error("del needs at least one url");
    await del(args);
    process.stdout.write(String(args.length));
    return;
  }

  throw new Error(`unknown command: ${cmd} (want list|del)`);
};

main().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
