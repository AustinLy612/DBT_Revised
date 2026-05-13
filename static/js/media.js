/* DBT Media - TTS playback, ASR recording, and Image generation client-side logic. */

(function () {
  "use strict";

  // ── State ──
  let isRecording = false;
  let mediaRecorder = null;
  let audioChunks = [];
  // Track which audio element is currently playing so we can stop it
  let currentAudio = null;
  let currentAudioMsgId = null;
  let isAudioPlaying = false;  // explicit flag — audio.paused is unreliable during buffering
  let _autoPlayPending = false;  // guard against double auto-play triggers
  // TTS auto-play: persisted in localStorage, default ON
  const AUTO_PLAY_STORAGE_KEY = "dbt_tts_autoplay";
  // Blob URL cache: avoids redundant TTS API calls for the same message
  const _blobCache = new Map();  // messageId → {blob: Blob, url: string}
  const BLOB_CACHE_MAX = 20;

  function _getAutoPlay() {
    return localStorage.getItem(AUTO_PLAY_STORAGE_KEY) !== "false";
  }

  function _setAutoPlay(enabled) {
    localStorage.setItem(AUTO_PLAY_STORAGE_KEY, enabled);
  }

  function _syncToggleUI() {
    var toggle = document.getElementById("tts-autoplay-toggle");
    if (toggle) {
      toggle.checked = _getAutoPlay();
    }
  }

  function _setButtonPlaying(msgId) {
    if (!msgId) return;
    var btn = document.getElementById("tts-btn-" + msgId);
    if (btn) { btn.disabled = false; btn.textContent = "⏹"; btn.title = "停止播放"; }
  }

  function _resetButton(msgId) {
    if (!msgId) return;
    var btn = document.getElementById("tts-btn-" + msgId);
    if (btn) { btn.disabled = false; btn.textContent = "🔊"; btn.title = "语音播报"; }
  }

  function _showAudioBar() {
    var bar = document.getElementById("audio-player-bar");
    if (!bar) return;
    // Reset any error styling
    bar.classList.remove("bg-red-50", "border-red-200");
    bar.classList.add("bg-blue-50", "border-blue-200");
    bar.style.display = "flex";
    bar.classList.remove("hidden");
    var status = document.getElementById("audio-bar-status");
    if (status) {
      status.textContent = "正在播放语音...";
      status.classList.remove("text-red-600");
      status.classList.add("text-gray-700");
    }
  }

  function _hideAudioBar() {
    var bar = document.getElementById("audio-player-bar");
    if (!bar) return;
    bar.style.display = "none";
    bar.classList.add("hidden");
    // Reset error styling for next time
    bar.classList.remove("bg-red-50", "border-red-200");
    bar.classList.add("bg-blue-50", "border-blue-200");
    var status = document.getElementById("audio-bar-status");
    if (status) {
      status.textContent = "正在播放语音...";
      status.classList.remove("text-red-600");
      status.classList.add("text-gray-700");
    }
  }

  // ── TTS: Play text as speech ──
  window.DBT_TTS = {
    play: function (text, messageId) {
      // If any audio is playing, stop it first
      if (isAudioPlaying) {
        var wasPlayingMsgId = currentAudioMsgId;
        this.stop();
        // If the clicked button is for the message that was just playing,
        // the user wants to stop — we're done
        if (messageId && wasPlayingMsgId === messageId) {
          return;
        }
        // Different message — fall through to play new audio
      }

      // ── Blob cache check ──
      if (messageId && _blobCache.has(messageId)) {
        var cached = _blobCache.get(messageId);
        _playAudioBlob(cached.blob, messageId, cached.url);
        return;
      }

      const btn = document.getElementById("tts-btn-" + messageId);
      if (btn) {
        btn.disabled = true;
        btn.textContent = "⟳";
        btn.title = "加载中...";
      }

      var formData = new FormData();
      formData.append("text", text);
      formData.append("message_id", messageId || "");

      // Use streaming endpoint for progressive playback
      _playAudioStream(formData, messageId, btn, text);
    },

    stop: function () {
      if (currentAudio) {
        currentAudio.pause();
        // Remove src to abort any ongoing network activity
        try { currentAudio.src = ""; currentAudio.load(); } catch(e) {}
      }
      var stoppedMsgId = currentAudioMsgId;
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
      _resetButton(stoppedMsgId);
    },

    /** Whether auto-play is currently enabled. */
    isAutoPlayEnabled: function () {
      return _getAutoPlay();
    },

    /** Toggle auto-play on/off. Returns the new state. Stops current playback if turning off. */
    toggleAutoPlay: function () {
      var next = !_getAutoPlay();
      _setAutoPlay(next);
      _syncToggleUI();
      // Stop any currently playing audio when turning auto-play off
      if (!next) {
        this.stop();
      }
      return next;
    },

    /** Auto-play the latest AI message in the chat area (only if toggle is ON). */
    autoPlayLatest: function () {
      if (!_getAutoPlay()) return;
      if (_autoPlayPending) return;  // guard against double-trigger
      var container = document.getElementById("chat-messages");
      if (!container) return;
      // Find the last assistant message via data-role attribute
      var allMessages = container.querySelectorAll("[data-role]");
      var lastAssistant = null;
      for (var i = allMessages.length - 1; i >= 0; i--) {
        if (allMessages[i].getAttribute("data-role") === "assistant") {
          lastAssistant = allMessages[i];
          break;
        }
      }
      if (!lastAssistant) return;
      var btn = lastAssistant.querySelector("button[id^='tts-btn-']");
      if (!btn) return;

      // Get text from the message bubble (direct child div of the wrapper)
      var bubble = lastAssistant.children[0] || lastAssistant;
      // Collect text from child nodes that are NOT the tts button or image containers
      var text = "";
      bubble.childNodes.forEach(function (node) {
        if (node.nodeType === Node.TEXT_NODE) {
          text += node.textContent;
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          if (node.tagName === "BR") { text += "\n"; }
          else if (!node.querySelector("button[id^='tts-btn-']") && !node.querySelector("img")) {
            text += node.textContent || "";
          }
        }
      });
      text = text.trim();
      if (!text) return;

      var msgId = btn.id.replace("tts-btn-", "");
      _autoPlayPending = true;
      this.play(text, msgId);
    },
  };

  // Sync toggle on load
  document.addEventListener("DOMContentLoaded", _syncToggleUI);

  function _playAudioBlob(blob, msgId, cachedUrl) {
    DBT_TTS.stop();
    var url = cachedUrl || URL.createObjectURL(blob);
    var isUrlFromCache = !!cachedUrl;
    var audio = new Audio(url);
    currentAudio = audio;
    currentAudioMsgId = msgId || null;
    isAudioPlaying = true;
    _autoPlayPending = false;
    _setButtonPlaying(msgId);
    _showAudioBar();
    audio.onended = function () {
      if (!isUrlFromCache) { URL.revokeObjectURL(url); }
      var endedMsgId = currentAudioMsgId;
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
      _resetButton(endedMsgId);
    };
    audio.play().catch(function (err) {
      console.error("Audio playback failed:", err);
      if (!isUrlFromCache) { URL.revokeObjectURL(url); }
      _resetButton(msgId);
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
    });
  }

  function _playAudioUrl(url, msgId) {
    DBT_TTS.stop();
    var audio = new Audio(url);
    currentAudio = audio;
    currentAudioMsgId = msgId || null;
    isAudioPlaying = true;
    _autoPlayPending = false;
    _setButtonPlaying(msgId);
    _showAudioBar();
    audio.onended = function () {
      var endedMsgId = currentAudioMsgId;
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
      _resetButton(endedMsgId);
    };
    audio.play().catch(function (err) {
      console.error("Audio playback from URL failed:", err);
      _resetButton(msgId);
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
    });
  }

  // ── Blob cache helper ──
  function _addToBlobCache(msgId, blob) {
    if (!msgId) return;
    if (_blobCache.size >= BLOB_CACHE_MAX) {
      var firstKey = _blobCache.keys().next().value;
      var old = _blobCache.get(firstKey);
      if (old && old.url) { URL.revokeObjectURL(old.url); }
      _blobCache.delete(firstKey);
    }
    var blobUrl = URL.createObjectURL(blob);
    _blobCache.set(msgId, { blob: blob, url: blobUrl });
  }

  // ── Progressive audio streaming via MediaSource ──
  function _playAudioStream(formData, msgId, btn, originalText) {
    DBT_TTS.stop();

    // Check for MediaSource support
    if (!window.MediaSource) {
      _fallbackToFetch(formData, msgId, btn, originalText);
      return;
    }

    var totalChunks = [];
    var streamOk = true;
    var mediaSource = new MediaSource();
    var mediaUrl = URL.createObjectURL(mediaSource);
    var sourceBuffer = null;
    var pendingChunks = [];
    var appending = false;
    var streamDone = false;

    var audio = new Audio(mediaUrl);
    currentAudio = audio;
    currentAudioMsgId = msgId || null;
    isAudioPlaying = true;
    _autoPlayPending = false;
    _setButtonPlaying(msgId);
    _showAudioBar();

    audio.onended = function () {
      var endedId = currentAudioMsgId;
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
      _resetButton(endedId);
      URL.revokeObjectURL(mediaUrl);
    };

    function _failStream(reason) {
      if (!streamOk) return;
      streamOk = false;
      console.warn("TTS stream failed (" + reason + "), falling back");
      try { mediaSource.endOfStream(); } catch (e) {}
      URL.revokeObjectURL(mediaUrl);
      // Reset playback flags so fallback can take over
      currentAudio = null;
      currentAudioMsgId = null;
      isAudioPlaying = false;
      _autoPlayPending = false;
      _hideAudioBar();
      _fallbackToFetch(formData, msgId, btn, originalText);
    }

    function _appendNext() {
      if (appending) return;
      if (pendingChunks.length === 0) {
        if (streamDone && mediaSource.readyState === "open") {
          try { mediaSource.endOfStream(); } catch (e) {}
        }
        return;
      }
      appending = true;
      try {
        sourceBuffer.appendBuffer(pendingChunks.shift());
      } catch (e) {
        console.error("SourceBuffer appendBuffer failed:", e);
        _failStream("appendBuffer");
      }
    }

    mediaSource.addEventListener("sourceopen", function () {
      try {
        sourceBuffer = mediaSource.addSourceBuffer("audio/mpeg");
        sourceBuffer.mode = "sequence";
      } catch (e) {
        _failStream("addSourceBuffer: " + e.message);
        return;
      }

      sourceBuffer.addEventListener("updateend", function () {
        appending = false;
        _appendNext();
      });

      sourceBuffer.addEventListener("error", function () {
        _failStream("sourceBuffer error");
      });

      // Start fetching the stream
      fetch("/media/tts/stream/", {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (resp) {
          if (!resp.ok) {
            _failStream("HTTP " + resp.status);
            return;
          }
          var reader = resp.body.getReader();
          function _readLoop() {
            reader.read().then(function (result) {
              if (result.done) {
                streamDone = true;
                _appendNext();
                // Cache the full blob when streaming completes successfully
                if (msgId && totalChunks.length > 0 && streamOk) {
                  var fullBlob = new Blob(totalChunks, { type: "audio/mpeg" });
                  _addToBlobCache(msgId, fullBlob);
                }
                return;
              }
              totalChunks.push(result.value);
              if (streamOk) {
                pendingChunks.push(result.value);
                _appendNext();
              }
              _readLoop();
            }).catch(function (err) {
              _failStream("read error: " + err.message);
            });
          }
          _readLoop();
        })
        .catch(function (err) {
          _failStream("fetch error: " + err.message);
        });
    });

    audio.play().catch(function (err) {
      console.error("Audio playback failed:", err);
      _failStream("play error: " + err.message);
    });
  }

  // ── Fallback: non-streaming fetch (original behavior) ──
  function _fallbackToFetch(formData, msgId, btn, originalText) {
    // If there's already a request in flight, use original text
    var fd = formData;
    if (originalText) {
      fd = new FormData();
      fd.append("text", originalText);
      fd.append("message_id", msgId || "");
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = "⟳";
      btn.title = "加载中...";
    }

    fetch("/media/tts/synthesize/", {
      method: "POST",
      body: fd,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(function (data) {
            throw new Error(data.error || "TTS request failed");
          });
        }
        var contentType = resp.headers.get("Content-Type") || "";
        if (contentType.includes("audio")) {
          return resp.blob().then(function (blob) {
            _addToBlobCache(msgId, blob);
            return { blob: blob };
          });
        }
        return resp.json();
      })
      .then(function (result) {
        if (result && result.blob) {
          var url = msgId ? (_blobCache.get(msgId) || {}).url : null;
          _playAudioBlob(result.blob, msgId, url);
        } else if (result && result.audio_url) {
          _playAudioUrl(result.audio_url, msgId);
        }
      })
      .catch(function (err) {
        console.error("TTS fallback error:", err);
        _autoPlayPending = false;
        if (btn) { btn.disabled = false; btn.textContent = "🔊"; btn.title = "语音播报"; }
        var bar = document.getElementById("audio-player-bar");
        var status = document.getElementById("audio-bar-status");
        if (bar) {
          bar.classList.remove("bg-blue-50", "border-blue-200", "hidden");
          bar.classList.add("bg-red-50", "border-red-200");
          bar.style.display = "flex";
        }
        if (status) {
          status.textContent = "语音播报失败";
          status.classList.add("text-red-600");
        }
        setTimeout(function () {
          if (bar) {
            bar.classList.add("hidden");
            bar.classList.add("bg-blue-50", "border-blue-200");
            bar.classList.remove("bg-red-50", "border-red-200");
            bar.style.display = "none";
          }
          if (status) {
            status.textContent = "正在播放语音...";
            status.classList.remove("text-red-600");
          }
        }, 5000);
      });
  }

  // ── ASR: Hybrid speech recognition ──
  //   1) Try browser SpeechRecognition (Google servers, fast & free)
  //   2) If network/service error (e.g. in China), fall back to server-side ASR
  window.DBT_ASR = {
    _recognition: null,

    isSupported: function () {
      return !!(
        (window.SpeechRecognition || window.webkitSpeechRecognition) ||
        (navigator.mediaDevices && navigator.mediaDevices.getUserMedia &&
         (window.MediaRecorder || window.webkitMediaRecorder))
      );
    },

    start: function (onStart, onError) {
      if (isRecording) return;

      var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      var chatInput = document.getElementById("chat-input");
      var statusEl = document.getElementById("asr-status");
      var self = this;

      if (SpeechRecognition) {
        // Path 1: Browser SpeechRecognition (works outside China)
        var rec = new SpeechRecognition();
        rec.lang = "zh-CN";
        rec.interimResults = true;
        rec.continuous = false;
        this._recognition = rec;

        rec.onresult = function (event) {
          var finalText = "";
          var interimText = "";
          for (var i = event.resultIndex; i < event.results.length; i++) {
            var t = event.results[i][0].transcript;
            if (event.results[i].isFinal) { finalText += t; }
            else { interimText += t; }
          }
          if (chatInput) {
            chatInput.value = finalText || interimText;
            if (finalText) chatInput.focus();
          }
          if (statusEl) {
            statusEl.textContent = finalText ? "✓ 识别完成" : "聆听中: " + interimText;
          }
        };

        rec.onerror = function (event) {
          console.warn("SpeechRecognition error:", event.error, "— falling back to server-side");
          self._recognition = null;
          isRecording = false;
          // SpeechRecognition failed for any reason — always try MediaRecorder fallback
          if (event.error !== "aborted") {
            if (statusEl) statusEl.textContent = "切换至录音模式...";
            self._startRecording(onStart, onError);
          }
        };

        rec.onend = function () {
          self._recognition = null;
          isRecording = false;
        };

        rec.start();
        isRecording = true;
        if (statusEl) statusEl.textContent = "聆听中...";
        if (onStart) onStart();
      } else {
        // No SpeechRecognition → go straight to server-side
        this._startRecording(onStart, onError);
      }
    },

    _startRecording: function (onStart, onError) {
      var statusEl = document.getElementById("asr-status");
      var self = this;
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(function (stream) {
          var Recorder = window.MediaRecorder || window.webkitMediaRecorder;
          var mimeType = "";
          var types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
          for (var i = 0; i < types.length; i++) {
            if (Recorder.isTypeSupported(types[i])) { mimeType = types[i]; break; }
          }
          var opts = {};
          if (mimeType) opts.mimeType = mimeType;
          mediaRecorder = new Recorder(stream, opts);
          audioChunks = [];

          mediaRecorder.ondataavailable = function (e) {
            if (e.data && e.data.size > 0) audioChunks.push(e.data);
          };

          mediaRecorder.onstop = function () {
            stream.getTracks().forEach(function (t) { t.stop(); });
            if (audioChunks.length === 0) return;
            var blob = new Blob(audioChunks, { type: mimeType || "audio/webm" });
            self._transcribeServer(blob);
          };

          mediaRecorder.start();
          isRecording = true;
          if (statusEl) statusEl.textContent = "录音中...";
          if (onStart) onStart();
        })
        .catch(function (err) {
          console.error("Microphone access failed:", err);
          if (statusEl) statusEl.textContent = "✗ 麦克风访问失败";
          if (onError) onError(err);
        });
    },

    _transcribeServer: function (audioBlob) {
      var chatInput = document.getElementById("chat-input");
      var statusEl = document.getElementById("asr-status");
      if (statusEl) statusEl.textContent = "识别中...";

      var formData = new FormData();
      formData.append("audio", audioBlob, "recording.webm");

      fetch("/media/asr/transcribe/", {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
          if (data.success) {
            if (chatInput) { chatInput.value = data.text; chatInput.focus(); }
            if (statusEl) statusEl.textContent = "✓ 识别完成";
          } else {
            if (statusEl) statusEl.textContent = "✗ " + (data.error || "语音识别失败");
          }
        })
        .catch(function (err) {
          console.error("ASR server request failed:", err);
          if (statusEl) statusEl.textContent = "✗ 请求失败，请检查网络";
        });
    },

    stop: function () {
      // Stop SpeechRecognition if active
      if (this._recognition) {
        this._recognition.stop();
        this._recognition = null;
      }
      // Stop MediaRecorder if active
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
      }
      isRecording = false;
    },

    isRecording: function () {
      return isRecording;
    },
  };

  // ── Chat helpers ──
  window.DBT_Chat = {
    /** Scroll the chat message container to the bottom. */
    scrollToBottom: function () {
      var container = document.getElementById("chat-messages");
      if (container) {
        requestAnimationFrame(function() {
          container.scrollTop = container.scrollHeight;
        });
      }
    },
  };

  // ── HTMX event handlers for chat behaviors ──
  // Using DOM event listeners instead of hx-on attribute for reliability
  document.addEventListener("htmx:afterSwap", function (evt) {
    var target = evt.detail.target;
    if (!target || target.id !== "chat-messages") return;
    // Clear the chat input
    var input = document.getElementById("chat-input");
    if (input) input.value = "";
    // Scroll to bottom after DOM settles
    requestAnimationFrame(function () {
      DBT_Chat.scrollToBottom();
    });
    // Auto-play TTS after a short delay (wait for scroll)
    setTimeout(function () {
      DBT_TTS.autoPlayLatest();
    }, 400);
  });

  // Scroll chat to bottom on initial load
  document.addEventListener("DOMContentLoaded", function () {
    DBT_Chat.scrollToBottom();
  });

  // ── SSE Streaming Chat ──
  window.DBT_Stream = {
    _metaRe: /<!--META:.*?-->/g,

    _escapeHtml: function (text) {
      var div = document.createElement("div");
      div.appendChild(document.createTextNode(text));
      return div.innerHTML;
    },

    send: function (event, streamUrl) {
      event.preventDefault();

      var form = event.target;
      var input = form.querySelector("input[name='message']");
      if (!input) return;
      var text = input.value.trim();
      if (!text) return;

      var chatContainer = document.getElementById("chat-messages");
      var sendBtn = document.getElementById("send-btn");
      var indicator = document.getElementById("sending-indicator");
      var csrfInput = form.querySelector("input[name='csrfmiddlewaretoken']");

      // Disable send button
      if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = "…"; }

      // Add user message bubble
      var userDiv = document.createElement("div");
      userDiv.className = "flex justify-end";
      userDiv.setAttribute("data-role", "user");
      userDiv.innerHTML = '<div class="max-w-[85%] rounded-lg px-4 py-2 text-sm bg-blue-500 text-white">' +
        text.replace(/\n/g, "<br>") + "</div>";
      chatContainer.appendChild(userDiv);

      // Clear input
      input.value = "";

      // Show skeleton
      if (indicator) indicator.style.display = "block";
      DBT_Chat.scrollToBottom();

      // Create assistant bubble placeholder
      var aiDiv = document.createElement("div");
      aiDiv.className = "flex justify-start";
      aiDiv.setAttribute("data-role", "assistant");
      var aiBubble = document.createElement("div");
      aiBubble.className = "max-w-[85%] rounded-lg px-4 py-2 text-sm bg-gray-100 text-gray-800";
      aiBubble.id = "streaming-bubble";
      aiBubble.innerHTML = '<span id="streaming-text"></span><span class="inline-block w-2 h-4 bg-gray-400 animate-pulse ml-0.5 align-text-bottom" id="streaming-cursor"></span>';
      aiDiv.appendChild(aiBubble);
      chatContainer.appendChild(aiDiv);
      DBT_Chat.scrollToBottom();

      // Build form body
      var formData = new FormData();
      formData.append("message", text);
      formData.append("csrfmiddlewaretoken", csrfInput ? csrfInput.value : "");

      var self = this;
      fetch(streamUrl, {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.text().then(function (t) { throw new Error("HTTP " + resp.status + ": " + t); });
          }
          return self._readStream(resp, aiBubble, indicator, sendBtn, chatContainer);
        })
        .catch(function (err) {
          console.error("Stream error:", err);
          var streamText = aiBubble.querySelector("#streaming-text");
          if (streamText) { streamText.innerHTML = self._escapeHtml("错误: " + err.message); streamText.id = ""; }
          var cursor = aiBubble.querySelector("#streaming-cursor");
          if (cursor) { cursor.style.display = "none"; cursor.id = ""; }
          if (indicator) indicator.style.display = "none";
          if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
          if (aiBubble) aiBubble.id = "";
        });
    },

    _readStream: function (resp, aiBubble, indicator, sendBtn, chatContainer) {
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      var accumulatedText = "";
      // Scope to aiBubble so each stream targets its own bubble
      var streamText = aiBubble.querySelector("#streaming-text");
      var cursor = aiBubble.querySelector("#streaming-cursor");
      var self = this;

      function _renderContent(text) {
        // Escape HTML, then convert \n to <br> for visible line breaks
        if (streamText) {
          streamText.innerHTML = self._escapeHtml(text).replace(/\n/g, "<br>");
        }
      }

      function processChunk() {
        reader.read().then(function (result) {
          if (result.done) {
            // Stream ended — clean up all IDs so next bubble's are unique
            if (cursor) { cursor.style.display = "none"; cursor.id = ""; }
            if (indicator) indicator.style.display = "none";
            if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
            if (streamText) streamText.id = "";
            if (aiBubble) aiBubble.id = "";
            // Scroll and trigger TTS auto-play
            DBT_Chat.scrollToBottom();
            setTimeout(function () { DBT_TTS.autoPlayLatest(); }, 400);
            return;
          }

          buffer += decoder.decode(result.value, { stream: true });
          var lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line || !line.startsWith("data: ")) continue;
            var jsonStr = line.substring(6);
            try {
              var event = JSON.parse(jsonStr);
              if (event.type === "content") {
                accumulatedText += event.text;
                // Strip META comment from display
                var clean = accumulatedText.replace(self._metaRe, "");
                _renderContent(clean);
                DBT_Chat.scrollToBottom();
              } else if (event.type === "done") {
                // Hide skeleton
                if (indicator) indicator.style.display = "none";
                if (cursor) { cursor.style.display = "none"; cursor.id = ""; }
                // Final content — use innerHTML for proper line breaks
                var finalText = accumulatedText.replace(self._metaRe, "");
                _renderContent(finalText);
                if (streamText) streamText.id = "";
                // Add TTS button
                var tc = event.teaching_content;
                var msgId = tc ? (tc.message_id || "") : "";
                if (aiBubble && msgId) {
                  aiBubble.id = "";
                  var ttsBtn = document.createElement("button");
                  ttsBtn.onclick = function () {
                    DBT_TTS.play(finalText, msgId);
                  };
                  ttsBtn.id = "tts-btn-" + msgId;
                  ttsBtn.className = "ml-2 text-xs text-gray-400 hover:text-blue-500 align-bottom";
                  ttsBtn.title = "语音播报";
                  ttsBtn.textContent = "🔊";
                  aiBubble.appendChild(ttsBtn);
                }
                // Check for risk
                if (tc && tc.should_stop_session) {
                  window.location.href = "/risk/popup/";
                  return;
                }
                // Handle image generation
                if (tc && tc.image_prompt) {
                  DBT_Image.generate(tc.image_prompt, "teaching-image-area", {
                    source: "teaching_scene",
                    session_id: window.location.pathname.split("/")[2] || "",
                  });
                }
              } else if (event.type === "error") {
                var errMsg = event.message || "未知错误";
                _renderContent(errMsg);
                if (cursor) { cursor.style.display = "none"; cursor.id = ""; }
                if (indicator) indicator.style.display = "none";
                if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
              }
            } catch (e) {
              // Skip malformed JSON lines
            }
          }
          processChunk();
        }).catch(function (err) {
          console.error("Stream read error:", err);
          if (cursor) { cursor.style.display = "none"; cursor.id = ""; }
          if (indicator) indicator.style.display = "none";
          if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
          if (streamText) streamText.id = "";
        });
      }

      processChunk();
    },
  };

  // ── Image Generation ──
  window.DBT_Image = {
    generate: function (prompt, targetId, extraParams) {
      var target = document.getElementById(targetId);
      if (!target) return;

      target.innerHTML =
        '<div class="text-sm text-gray-400 animate-pulse">正在生成图片...</div>';

      var formData = new FormData();
      formData.append("prompt", prompt);
      if (extraParams) {
        Object.keys(extraParams).forEach(function (k) {
          formData.append(k, extraParams[k]);
        });
      }

      fetch("/media/image/generate/", {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (resp) {
          return resp.text();
        })
        .then(function (html) {
          target.innerHTML = html;
          // Also append the generated image to the chat area (centered)
          var chatContainer = document.getElementById("chat-messages");
          if (chatContainer && extraParams && extraParams.source === "teaching_scene") {
            var msgDiv = document.createElement("div");
            msgDiv.className = "flex justify-center";
            msgDiv.innerHTML = '<div class="bg-purple-50 border border-purple-200 rounded-lg px-4 py-3 max-w-[85%]">' +
              '<p class="text-xs text-purple-500 mb-2">生成的教学配图</p>' +
              html.replace('generated-image', '') +
              '</div>';
            chatContainer.appendChild(msgDiv);
            DBT_Chat.scrollToBottom();
          }
        })
        .catch(function (err) {
          target.innerHTML =
            '<div class="text-red-500 text-sm">图片生成失败: ' +
            err.message +
            "</div>";
        });
    },
  };
})();
