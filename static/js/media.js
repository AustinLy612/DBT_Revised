/* DBT Media - TTS playback, ASR recording, and Image generation client-side logic. */

(function () {
  "use strict";

  // ── State ──
  let isRecording = false;
  let mediaRecorder = null;
  let audioChunks = [];
  // Track which audio element is currently playing so we can stop it
  let currentAudio = null;
  // TTS auto-play: persisted in localStorage, default ON
  const AUTO_PLAY_STORAGE_KEY = "dbt_tts_autoplay";

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

  // ── TTS: Play text as speech ──
  window.DBT_TTS = {
    play: function (text, messageId) {
      const btn = document.getElementById("tts-btn-" + messageId);
      if (btn) {
        btn.disabled = true;
        btn.textContent = "⟳";
      }

      const formData = new FormData();
      formData.append("text", text);
      formData.append("message_id", messageId || "");

      fetch("/media/tts/synthesize/", {
        method: "POST",
        body: formData,
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
              return { blob: blob };
            });
          }
          return resp.json();
        })
        .then(function (result) {
          if (result.blob) {
            _playAudioBlob(result.blob);
          } else if (result.audio_url) {
            _playAudioUrl(result.audio_url);
          }
        })
        .catch(function (err) {
          console.error("TTS error:", err);
          alert("语音播报失败: " + err.message);
        })
        .finally(function () {
          if (btn) {
            btn.disabled = false;
            btn.textContent = "🔊";
          }
        });
    },

    stop: function () {
      if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
      }
    },

    /** Whether auto-play is currently enabled. */
    isAutoPlayEnabled: function () {
      return _getAutoPlay();
    },

    /** Toggle auto-play on/off. Returns the new state. */
    toggleAutoPlay: function () {
      var next = !_getAutoPlay();
      _setAutoPlay(next);
      _syncToggleUI();
      return next;
    },

    /** Auto-play the latest AI message in the chat area (only if toggle is ON). */
    autoPlayLatest: function () {
      if (!_getAutoPlay()) return;
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
      if (btn) {
        var text = lastAssistant.textContent.replace(/🔊$/, "").trim();
        this.play(text, btn.id.replace("tts-btn-", ""));
      }
    },
  };

  // Sync toggle on load
  document.addEventListener("DOMContentLoaded", _syncToggleUI);

  function _playAudioBlob(blob) {
    DBT_TTS.stop();
    var url = URL.createObjectURL(blob);
    var audio = new Audio(url);
    currentAudio = audio;
    audio.onended = function () {
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
    audio.play().catch(function (err) {
      console.error("Audio playback failed:", err);
    });
  }

  function _playAudioUrl(url) {
    DBT_TTS.stop();
    var audio = new Audio(url);
    currentAudio = audio;
    audio.onended = function () {
      currentAudio = null;
    };
    audio.play().catch(function (err) {
      console.error("Audio playback from URL failed:", err);
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
    }, 350);
  });

  // Scroll chat to bottom on initial load
  document.addEventListener("DOMContentLoaded", function () {
    DBT_Chat.scrollToBottom();
  });

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
