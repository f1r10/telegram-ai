"use strict";
(() => {
  const tg = window.Telegram?.WebApp;
  const $ = (id) => document.getElementById(id);
  const ui = {
    connection: $("connection"), employee: $("employee"), conversation: $("conversation"),
    question: $("question"), send: $("send"), composer: $("composer"),
    intro: $("intro"), scopeTitle: $("scope-title"), scopeKicker: $("scope-kicker"),
    scopeNote: $("scope-note"), suggestions: $("suggestions"), modes: $("modes"),
    locked: $("locked"), lockedCopy: $("locked-copy"),
    salesFields: $("sales-fields"), salesYear: $("sales-year"),
    uploadPanel: $("upload-panel"), uploadFile: $("upload-file"),
    uploadPrepare: $("upload-prepare"), uploadPreview: $("upload-preview"),
    uploadName: $("upload-name"), uploadSize: $("upload-size"),
    uploadConfirm: $("upload-confirm"), uploadCancel: $("upload-cancel"),
    uploadFeedback: $("upload-feedback")
  };
  let scope = "chat";
  let busy = false;
  let active = false;
  let uploadTicket = null;
  const signed = typeof tg?.initData === "string" ? tg.initData : "";
  if (tg) {
    tg.ready();
    tg.expand();
    tg.onEvent?.("themeChanged", () => {
      document.documentElement.dataset.telegramTheme = tg.colorScheme || "light";
    });
  }

  function lock(message) {
    active = false;
    ui.lockedCopy.textContent = message ||
      "Bu tətbiqi Telegram botunuzdan yenidən açın. Giriş təsdiqlənmədən şirkət məlumatına baxmaq mümkün deyil.";
    ui.locked.hidden = false;
    ui.connection.textContent = "Giriş tələb olunur";
    ui.send.disabled = true;
  }

  async function request(path, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 78000);
    try {
      const response = await fetch(path, {
        method: data === undefined ? "GET" : "POST",
        cache: "no-store",
        credentials: "omit",
        headers: {
          "X-Telegram-Init-Data": signed,
          ...(data === undefined ? {} : {"Content-Type": "application/json"})
        },
        ...(data === undefined ? {} : {body: JSON.stringify(data)}),
        signal: controller.signal
      });
      if (response.status === 401) {
        lock("Giriş müddəti bitib və ya icazəniz yoxdur. Mini App-ı Telegram botundan yenidən açın.");
        throw new Error("Giriş tələb olunur.");
      }
      const result = await response.json();
      if (!response.ok) {
        if (response.status === 429) throw new Error("Çox sayda sorğu göndərildi. Bir qədər sonra yenidən cəhd edin.");
        if (response.status === 400) throw new Error("Məlumatı yoxlayın və yenidən yazın.");
        if (response.status === 409) throw new Error("Başqa sənəd sessiyası açıqdır. Əvvəl Telegram botunda /saxla və ya /cancel edin.");
        if (response.status === 410) throw new Error("Fayl təsdiqinin vaxtı bitib. Yenidən seçin.");
        throw new Error("Xidmət hazırda əlçatan deyil. Telegram botunda sual verməyə davam edə bilərsiniz.");
      }
      return result;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function downloadSource(token, button) {
    if (!active || busy || typeof token !== "string" || !token) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Yüklənir…";
    try {
      const response = await fetch("/api/source", {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: {
          "X-Telegram-Init-Data": signed,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({token}),
        signal: controller.signal
      });
      if (response.status === 401) {
        lock("Bu sənəd üçün Telegram icazəsi etibarsızdır. Botdan yenidən daxil olun.");
        return;
      }
      if (!response.ok) throw new Error("Mənbə faylı artıq mövcud deyil və ya icazəniz yoxdur.");
      const content = response.headers.get("Content-Disposition") || "";
      const match = content.match(/filename\*=UTF-8''([^;]+)/i);
      let filename = "menbe-fayli.bin";
      if (match) {
        try { filename = decodeURIComponent(match[1]).replace(/[/\\\r\n]/g, "_"); }
        catch (_) { filename = "menbe-fayli.bin"; }
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      button.textContent = "Endirildi ✓";
    } catch (error) {
      appendBubble(error?.message || "Faylı endirmək mümkün olmadı.", "assistant error");
      button.textContent = oldText;
    } finally {
      clearTimeout(timeout);
      button.disabled = !active;
    }
  }

  function appendBubble(text, kind, sources = []) {
    const bubble = document.createElement("article");
    bubble.className = "bubble " + kind;
    bubble.textContent = text;
    if (sources.length) {
      const footer = document.createElement("div");
      footer.className = "sources";
      const lead = document.createElement("strong");
      lead.textContent = "Yoxlanılan mənbələr";
      footer.append(lead);
      for (const source of sources) {
        if (!Number.isInteger(source.number) || typeof source.label !== "string") continue;
        const row = document.createElement("div");
        row.className = "source";
        const number = document.createElement("strong");
        number.textContent = "[" + source.number + "]";
        const label = document.createElement("span");
        label.textContent = source.label;
        row.append(number, label);
        if (typeof source.download_token === "string" && source.download_token) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "source-download";
          button.textContent = "Faylı endir ↓";
          button.setAttribute("aria-label", source.number + "-ci mənbənin orijinalını endir");
          button.addEventListener("click", () => {
            void downloadSource(source.download_token, button);
          });
          row.append(button);
        }
        footer.append(row);
      }
      bubble.append(footer);
    }
    ui.conversation.append(bubble);
    ui.conversation.scrollTop = ui.conversation.scrollHeight;
    return bubble;
  }

  const screens = {
    chat: {
      kicker: "AĞILLI OFİS SÖHBƏTİ", title: "Nəyi öyrənmək istəyirsiniz?",
      placeholder: "Sualınızı buraya yazın...",
      note: "Cavablar mövcud mənbələrdən asılıdır. Şirkət məlumatını internetə avtomatik göndərmirik.",
      samples: ["KA45 haqqında nə bilirik?", "Göndərdiyim sənəd barədə məlumat ver"]
    },
    baza: {
      kicker: "ŞİRKƏTİNİZİN MƏLUMATLARI", title: "Bizim bazada nə axtaraq?",
      placeholder: "Məhsul, sənəd və ya qiymət haqqında soruşun...",
      note: "Yalnız şirkətin daxili məlumatları. Mənbə tapılmazsa bunu açıq bildirəcəyəm.",
      samples: ["KA45 barədə hansı sənədlərimiz var?", "Son qiymət qeydləri nə deyir?"]
    },
    product: {
      kicker: "ŞİRKƏTİN MƏHSUL KATALOQU", title: "Hansı məhsulu axtarırsınız?",
      placeholder: "Məhsul kodu və ya təsdiqlənmiş adı...",
      note: "Burada məhsulun yalnız təsdiqlənmiş kataloq məlumatları göstərilir. Kataloqda olması stok və ya qiymət zəmanəti deyil.",
      samples: ["KA45", "QA-KA45"]
    },
    stock: {
      kicker: "TƏSDİQLƏNMİŞ ANBAR UÇOTU", title: "Hansı məhsulun qalığını yoxlayaq?",
      placeholder: "Məhsul kodu, məsələn: KA45",
      note: "Stok qiymət təklifindən və ya kataloqdan çıxarılmır. Açılış qalığı yoxdursa «məlum deyil» göstərilir.",
      samples: ["KA45", "QA-KA45"]
    },
    sales: {
      kicker: "SATIŞ FAKTURALARI", title: "Hansı müştərinin satışına baxaq?",
      placeholder: "Müştərinin adını buraya yazın...",
      note: "Yalnız təsdiqlənmiş satış fakturaları; valyutalar qarışdırılmır. Qeyd yoxdursa sıfır satış faktı iddia edilmir.",
      samples: ["A şirkəti", "B şirkəti"]
    },
    upload: {
      kicker: "SƏNƏDİNİZİ ƏLAVƏ EDİN", title: "Faylınızı yoxlayın və təsdiqləyin",
      placeholder: "",
      note: "Fayl əvvəlcə yalnız müvəqqəti saxlanılır. «Təsdiqlə və saxla» seçilmədən şirkət bazasına yazılmır.",
      samples: []
    }
  };

  function selectScope(next) {
    if (busy || !screens[next]) return;
    if (uploadTicket && next !== "upload") {
      ui.uploadFeedback.textContent = "Əvvəl faylı təsdiqləyin və ya ləğv edin.";
      return;
    }
    scope = next;
    for (const button of ui.modes.querySelectorAll("button[data-scope]")) {
      const chosen = button.dataset.scope === scope;
      button.classList.toggle("active", chosen);
      button.setAttribute("aria-pressed", String(chosen));
    }
    const config = screens[scope];
    ui.scopeKicker.textContent = config.kicker;
    ui.scopeTitle.textContent = config.title;
    ui.scopeNote.textContent = config.note;
    ui.question.placeholder = config.placeholder;
    ui.question.value = "";
    ui.salesFields.hidden = scope !== "sales";
    ui.uploadPanel.hidden = scope !== "upload";
    ui.composer.hidden = scope === "upload";
    ui.suggestions.hidden = scope === "upload";
    if (scope === "sales" && !ui.salesYear.value) {
      ui.salesYear.value = String(new Date().getFullYear());
    }
    ui.suggestions.replaceChildren();
    for (const value of config.samples) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = value + " ↗";
      button.addEventListener("click", () => { ui.question.value = value; ui.question.focus(); });
      ui.suggestions.append(button);
    }
    ui.question.focus();
  }

  function uploadStatus(message, failed = false) {
    ui.uploadFeedback.textContent = message;
    ui.uploadFeedback.classList.toggle("error", failed);
  }

  function resetUpload() {
    uploadTicket = null;
    ui.uploadPreview.hidden = true;
    ui.uploadFile.value = "";
    ui.uploadPrepare.disabled = false;
    ui.uploadConfirm.disabled = false;
    ui.uploadCancel.disabled = false;
  }

  async function prepareUpload() {
    if (!active || busy || uploadTicket) return;
    const file = ui.uploadFile.files?.[0];
    if (!file) { uploadStatus("Əvvəlcə fayl seçin.", true); return; }
    if (file.size < 1 || file.size > 8 * 1024 * 1024 ||
        file.name.length > 160 ||
        !/\.(pdf|docx|xlsx|txt|jpg|jpeg|png)$/i.test(file.name)) {
      uploadStatus("Dəstəklənən növdə, 8 MB-dan kiçik fayl seçin.", true);
      return;
    }
    busy = true;
    ui.uploadPrepare.disabled = true;
    uploadStatus("Fayl təhlükəsiz yoxlanılır…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch("/api/upload/prepare", {
        method: "POST", cache: "no-store", credentials: "omit",
        headers: {
          "X-Telegram-Init-Data": signed,
          "X-Office-Filename": encodeURIComponent(file.name),
          "Content-Type": file.type || "application/octet-stream"
        },
        body: file, signal: controller.signal
      });
      if (response.status === 401) {
        lock("Giriş etibarsızdır. Telegram botundan yenidən açın.");
        return;
      }
      if (response.status === 409) {
        throw new Error("Botda açıq sənəd sessiyası var və ya əvvəlki fayl hələ təsdiqlənməyib. Əvvəl /saxla və ya /cancel edin.");
      }
      if (!response.ok) throw new Error("Fayl qəbul edilmədi. Formatı və həcmi yoxlayın.");
      const preview = await response.json();
      if (typeof preview.token !== "string") throw new Error("Təsdiq məlumatı alınmadı.");
      uploadTicket = preview.token;
      ui.uploadName.textContent = preview.filename;
      ui.uploadSize.textContent = (preview.size / 1024).toFixed(1) + " KB · Yalnız müvəqqəti saxlanılıb";
      ui.uploadPreview.hidden = false;
      uploadStatus("Fayl yoxlanıldı. Bazaya əlavə etmək üçün təsdiqləyin.");
    } catch (error) {
      uploadStatus(error?.message || "Fayl göndərilmədi.", true);
    } finally {
      clearTimeout(timeout);
      busy = false;
      ui.uploadPrepare.disabled = !active || Boolean(uploadTicket);
    }
  }

  async function resolveUpload(confirm) {
    if (!active || busy || !uploadTicket) return;
    const token = uploadTicket;
    busy = true;
    ui.uploadConfirm.disabled = true;
    ui.uploadCancel.disabled = true;
    uploadStatus(confirm ? "Təsdiqlənmiş fayl saxlanılır…" : "Müvəqqəti fayl silinir…");
    try {
      const result = await request(
        confirm ? "/api/upload/confirm" : "/api/upload/cancel", {token}
      );
      resetUpload();
      uploadStatus(result.notice || (confirm ? "Emal növbəsinə verildi." : "Ləğv edildi."));
      if (confirm) {
        appendBubble(
          "Sənəd qəbul edildi. Emal işi №" + result.job_id +
          " yaradıldı. Sənədin oxunması və axtarışa daxil edilməsi ayrıca davam edir.",
          "assistant"
        );
      }
    } catch (error) {
      const message = error?.message || "Əməliyyat tamamlanmadı.";
      uploadStatus(message + (confirm ? " Nəticəni botda /saxla ilə yoxlayın." : ""), true);
      if (confirm) resetUpload();
    } finally {
      busy = false;
      ui.uploadConfirm.disabled = !active;
      ui.uploadCancel.disabled = !active;
    }
  }

  ui.uploadPrepare.addEventListener("click", () => { void prepareUpload(); });
  ui.uploadConfirm.addEventListener("click", () => { void resolveUpload(true); });
  ui.uploadCancel.addEventListener("click", () => { void resolveUpload(false); });

  async function submit(text) {
    if (!active || busy || !text.trim()) return;
    const sent = text.trim();
    const currentScope = scope;
    busy = true;
    ui.send.disabled = true;
    ui.question.value = "";
    ui.suggestions.hidden = true;
    appendBubble(sent, "mine");
    const pending = appendBubble("Mənbələr yoxlanılır…", "assistant");
    try {
      if (currentScope === "stock") {
        const stock = await request("/api/stock", {code: sent});
        const lines = stock.known
          ? [stock.product_code, ...stock.locations.map(x => x.warehouse + ": " + x.quantity), "Cəmi: " + stock.total]
          : [stock.product_code, "Qalıq MƏLUM DEYİL"];
        pending.textContent = lines.join("\n") + "\n\n" + stock.notice;
      } else if (currentScope === "product") {
        const result = await request("/api/product", {query: sent});
        if (result.status === "found" && result.product) {
          pending.textContent = [result.product.code + " · " + result.product.name,
            result.product.description || "Əlavə təsvir yoxdur.",
            "Məhsul kataloqda təsdiqlənib. Anbar qalığı və qiymət ayrıca yoxlanmalıdır."].join("\n\n");
        } else if (result.status === "ambiguous") {
          pending.textContent = "Bir neçə uyğun məhsul tapıldı. Aşağıdakı kodlardan birini seçin:";
          for (const candidate of result.candidates || []) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "candidate";
            button.textContent = candidate.code + " — " + candidate.name;
            button.addEventListener("click", () => {
              selectScope("product");
              ui.question.value = candidate.code;
              ui.question.focus();
            });
            pending.append(button);
          }
        } else {
          pending.textContent = "Bu ad və ya kod üzrə təsdiqlənmiş məhsul tapılmadı. Kodu yoxlayın və ya «Bizim baza»da sual verin.";
        }
      } else if (currentScope === "sales") {
        const year = Number(ui.salesYear.value);
        if (!Number.isInteger(year) || year < 2000 || year > new Date().getFullYear()) {
          throw new Error("İli düzgün seçin.");
        }
        const result = await request("/api/sales", {customer: sent, year});
        const rows = result.has_records
          ? result.totals.map(x => x.invoice_count + " faktura · " + x.amount + " " + x.currency)
          : ["Təsdiqlənmiş satış qeydi tapılmadı."];
        pending.textContent = [result.customer + " · " + result.year, ...rows,
          result.notice].join("\n\n");
      } else {
        const result = await request("/api/ask", {question: sent, scope: currentScope});
        pending.remove();
        appendBubble(result.answer || "Bu suala əsaslandırılmış cavab tapılmadı.",
          "assistant", Array.isArray(result.sources) ? result.sources : []);
      }
    } catch (error) {
      pending.remove();
      if (active) appendBubble(error?.message || "Sorğunu tamamlamadım. Yenidən cəhd edin.", "assistant error");
    } finally {
      busy = false;
      ui.send.disabled = !active;
      if (active) ui.question.focus();
    }
  }

  ui.modes.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-scope]");
    if (button) selectScope(button.dataset.scope);
  });
  ui.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    void submit(ui.question.value);
  });
  ui.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ui.composer.requestSubmit();
    }
  });
  ui.suggestions.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-example]");
    if (button) { ui.question.value = button.dataset.example; ui.question.focus(); }
  });
  $("new-chat").addEventListener("click", () => {
    if (busy) return;
    ui.conversation.replaceChildren();
    appendBubble("Yeni sualınızı yazın. Əvvəlki cavablar bu ekrandan təmizləndi; məlumat bazası dəyişməyib.", "assistant");
    ui.suggestions.hidden = false;
  });
  $("back-to-chat").addEventListener("click", () => tg?.close?.());
  $("reload").addEventListener("click", () => window.location.reload());

  if (!signed) {
    lock("Telegram tərəfindən təsdiqlənmiş giriş tapılmadı. Bu səhifəni adi brauzerdən deyil, şirkətinizin Telegram botundakı Mini App düyməsindən açın.");
    return;
  }
  selectScope("chat");
  request("/api/me").then((profile) => {
    active = true;
    ui.send.disabled = false;
    ui.employee.textContent = profile.display_name + (profile.role === "admin" ? " · Admin" : "");
    ui.connection.textContent = "Təhlükəsiz bağlantı";
    ui.question.focus();
  }).catch(() => {
    if (!ui.locked.hidden) return;
    lock("Serverə təhlükəsiz qoşulmaq mümkün olmadı. Telegram botu mövcud söhbət üçün işləməyə davam edir.");
  });
})();
