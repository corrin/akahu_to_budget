# Changelog

## 0.1.2

- Add Home Assistant add-on changelog metadata so the app store can display
  release notes.
- Clarify the add-on setup documentation around checking startup logs.

## 0.1.1

- Add a `mapping_json` option for one-time copy/paste or MCP-driven mapping
  bootstrap.
- Keep `mapping_json` as raw JSON and write it to `mapping_file` only when the
  mapping file is missing.
- Document the HAOS mapping setup paths.

## 0.1.0

- Add the initial Home Assistant OS add-on wrapper.
- Publish the HAOS image as `ghcr.io/corrin/akahu_to_budget-haos`.
