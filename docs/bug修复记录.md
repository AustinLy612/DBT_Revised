# Image generation failure in teaching materials
_Exported on 6/19/2026 at 16:22:02 GMT+8 from Cursor (3.6.31)_

---

**User**

现在读取当前项目文档@memory_bank 与系统架构及内容 目前在教学过程中图片生成失败 显示：生成的教学配图 图像生成失败，请稍后再试。
准确定位并解决问题

---

**Cursor**

I'll start by reading the project documentation and understanding the architecture.

Now let me locate the image generation code in the project.

Now let me look at the image task, views, and the error message location.

Let me look at the views.py for image generation to understand the failure path.

The actual root cause is captured in `ImageGenerationLog.error_message`. Let me check settings config and recent error logs.

Let me check the actual `.env` config and any log files to find the concrete error.

Let me search the current log for the actual Jimeng error.

Found it. The current error is `SignatureDoesNotMatch` (100010). Earlier it was `InvalidSecretToken`. Let me check the credential format currently in `.env`.

Let me check only the structure (number of parts) without exposing key material.

The signing code itself looks correct. The logs show the credential error evolved from `InvalidSecretToken` (expired/broken STS token) → `SignatureDoesNotMatch`. Let me run a live diagnostic through the app to capture the current exact error (without printing any secret values), and confirm whether it's a credential vs. signing issue.

I can see prior context: the user switched to a long-term Volcengine key (AK starting `AKLT`). The current error is `SignatureDoesNotMatch`. Let me run a definitive live diagnostic through the app to capture the exact current failure.

The auto-review is blocking live execution. I have enough from the logs (`SignatureDoesNotMatch`). Let me verify the signing algorithm against the official Volcengine spec, because I suspect a bug in the signing-key derivation. Let me confirm the exact Volcengine Signature V4 algorithm.

Confirmed. I found the root cause. The official Volcengine Signature V4 spec (from their docs and `volcengine/volc-openapi-demos`) derives the signing key as:

```
kSecret = <Your Secret Access Key>
kDate   = HMAC(kSecret, Date)     # secret_key used DIRECTLY
```

But the code prepends `"VOLC"` (AWS-style, where AWS uses `"AWS4"+sk`). Volcengine does **not** do this. That mismatch produces exactly `SignatureDoesNotMatch`. This bug was previously masked because the older STS credentials failed earlier at the token check (`InvalidSecretToken`); after switching to a long-term `AKLT...` key, the request gets past the token stage and now exposes the signing bug.

Let me fix it.

Now let me check the test file to confirm expected signing behaviour, and fix the separate `aspect_ratio` bug in the async task.

Tests mock the signer, so the fix won't break them. Now let me check the separate `aspect_ratio` bug in `media_app/tasks.py` — `generate_image()` has no such parameter, so that call would raise `TypeError`. Let me check if it's used.

The async task (used at `teaching/views.py:564`) also has a bug: it passes `aspect_ratio="1:1"`, which `generate_image()` does not accept — that raises `TypeError` and silently fails the auto-triggered teaching image. Let me fix that too.

Now let me verify the full signing function reads correctly after the edit, and clean up the diagnostic file.

Fix is in place. Let me run the `media_app` unit tests to confirm no regression.

Django isn't in the base env. Let me find the right conda environment.

Let me just verify syntax without any key material.

---

**User**

我同意

---

**Cursor**

你同意了，我来跑一次真实的图片生成验证修复是否生效。

---

**User**

继续推进

---

**Cursor**

The run was interrupted. Let me check the log to see if the diagnostic actually reached the API.

No new log entry appeared — the previous run seems to have been interrupted before completing (or its stdout was lost). Let me re-run the diagnostic cleanly now and capture the result directly.

日志确认最后一次失败仍是修复前的 SignatureDoesNotMatch，之后没有新的图片接口日志。下一步我会用项目的 dbt 环境做本地语法/测试验证，再决定是否需要重启服务让改动生效。