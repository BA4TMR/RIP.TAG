# Vendored front-end dependency

`html5-qrcode.min.js` is html5-qrcode v2.3.8, downloaded from
`https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js`.

The library is licensed under Apache License 2.0. It is vendored locally so the
event does not depend on a third-party CDN or public network access at the
venue, and so the scanned URL/token is never exposed through a Referer header.

## dtpweb browser SDK

The printer vendor's SDK is not bundled here because it is distributed with
the dtpweb print assistant / printer vendor. If you have a local copy, place it
at `app/static/vendor/dtpweb.umd.js` and set:

```dotenv
DTPWEB_SDK_URL=/static/vendor/dtpweb.umd.js
```

The production page also detects an SDK already injected by another script:
`window.DTPWeb`, `window.dtpweb`, or `window.LPAPIFactory`.
