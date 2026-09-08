---
name: git-push-proxy-issue
description: HTTP_PROXY/HTTPS_PROXY env vars block Git push
type: reference
---

`HTTP_PROXY` and `HTTPS_PROXY` are set to `http://127.0.0.1:9000`. This blocks `git push` to GitHub. Run `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy && git push <remote> <branch>` to bypass.

**Why:** A local proxy at 127.0.0.1:9000 may be configured but not always running.

**How to apply:** Before `git push`, unset the proxy variables. Or for this project, configure git to not use the proxy: `git config --local http.proxy ""`.
