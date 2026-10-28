# FlowSME website

A one-page static site: plain HTML and CSS, no build step. Open `index.html` in a browser to see it.

- `index.html`: the page, with its styles inline, schema.org data (Organization, WebSite, SoftwareApplication,
  FAQPage), Open Graph and Twitter card tags. The FAQ answers in the schema must match the FAQ on the page.
- `sample-report.html` and `shots/`: a copy of `examples/demo-invoice-processing/analysis/report.html` and its
  screenshots (synthetic data). It is marked `noindex`.
- `og-image.png` (1200x630, for link previews), `favicon.svg`, `apple-touch-icon.png`, `icon-512.png`.
- `robots.txt`, `sitemap.xml`.

## Before going live

The domain and the contact address are placeholders. Replace them everywhere in this folder:

- `https://flowsme.example` with the real address (in `index.html`, `robots.txt`, `sitemap.xml`)
- `hello@flowsme.example` with the real contact address (in `index.html`)

## Deploy

- **Cloudflare Pages:** create a project, connect the repository, leave the build command empty and set the
  output directory to `site`. Or upload the folder with `npx wrangler pages deploy site`.
- **Netlify:** set the publish directory to `site` with no build command, or drag the folder onto
  app.netlify.com/drop.
