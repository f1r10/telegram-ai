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
    uploadFeedback: $("upload-feedback"),
    jobsRefresh: $("jobs-refresh"), jobList: $("job-list"),
    photoPanel: $("photo-panel"), photoFile: $("photo-file"),
    photoSearch: $("photo-search"), photoWebSearch: $("photo-web-search"),
    photoFeedback: $("photo-feedback"),
    photoResults: $("photo-results"),
    transferMode: $("transfer-mode"), transferPanel: $("transfer-panel"),
    transferPreview: $("transfer-preview"), transferDetails: $("transfer-details"),
    transferFeedback: $("transfer-feedback"), transferPrepare: $("transfer-prepare"),
    transferConfirm: $("transfer-confirm"), transferCancel: $("transfer-cancel"),
    actionMode: $("stock-action-mode"), actionPanel: $("stock-action-panel"),
    actionKind: $("stock-action-kind"), actionReasonField: $("stock-action-reason-field"),
    actionPreview: $("stock-action-preview"), actionDetails: $("stock-action-details"),
    actionFeedback: $("stock-action-feedback"), actionPrepare: $("stock-action-prepare"),
    actionConfirm: $("stock-action-confirm"), actionCancel: $("stock-action-cancel"),
    xlsxMode: $("xlsx-mode"), xlsxPanel: $("stock-xlsx-panel"),
    xlsxFile: $("stock-xlsx-file"), xlsxPrepare: $("stock-xlsx-prepare"),
    xlsxPreview: $("stock-xlsx-preview"), xlsxSummary: $("stock-xlsx-summary"),
    xlsxDigest: $("stock-xlsx-digest"), xlsxRows: $("stock-xlsx-rows"),
    xlsxConfirm: $("stock-xlsx-confirm"), xlsxCancel: $("stock-xlsx-cancel"),
    xlsxFeedback: $("stock-xlsx-feedback")
  };
  let scope = "chat";
  let busy = false;
  let active = false;
  let uploadTicket = null;
  let transferTicket = null;
  let transferBusy = false;
  let canTransfer = false;
  let canStockActions = false;
  let actionTicket = null;
  let actionBusy = false;
  let canXlsx = false;
  let xlsxBusy = false;
  let xlsxTicket = null;
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
        if (response.status === 409) throw new Error(
          path.startsWith("/api/stock/")
            ? "Stok və ya təsdiq dəyişib. Tarixçəni yoxlayıb yenidən önbaxış edin."
            : "Başqa sənəd sessiyası açıqdır. Əvvəl Telegram botunda /saxla və ya /cancel edin."
        );
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
    photo: {
      kicker: "ŞƏKİLLƏ MƏHSUL AXTARIŞI", title: "Məhsulun şəklini göndərin",
      placeholder: "",
      note: "Yalnız təsdiqli aktiv fotolarla müqayisə edilir. Oxşarlıq məhsulun, qiymətin və stokun təsdiqi deyil.",
      samples: []
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
    "stock-xlsx": {
      kicker: "ADMİN · EXCEL STOK İDXALI", title: "Bütün sətirləri yoxlayıb təsdiqləyin",
      placeholder: "",
      note: "Yalnız 5 və ya 6 sütunlu təsdiqlənmiş stok şablonları. Cəmi 200 sətir. Təsdiqə qədər stok dəyişmir.",
      samples: []
    },
    "stock-action": {
      kicker: "ADMİN · STOK TƏSDİQİ", title: "Anbar əməliyyatını yoxlayın",
      placeholder: "",
      note: "Yeni rezervasiya, rezervasiyanın ləğvi/istifadəsi və stok düzəlişləri ayrıca admin təsdiqi tələb edir.",
      samples: []
    },
    transfer: {
      kicker: "ADMİN · ANBARLARARASI KÖÇÜRMƏ", title: "Köçürməni önbaxışda yoxlayın",
      placeholder: "",
      note: "Bu əməliyyat yalnız administratorun ayrıca təsdiqi ilə həyata keçirilir. Təsdiq zamanı stok yenidən yoxlanılır.",
      samples: []
    },
    upload: {
      kicker: "SƏNƏDİNİZİ ƏLAVƏ EDİN", title: "Faylınızı yoxlayın və təsdiqləyin",
      placeholder: "",
      note: "Fayl əvvəlcə yalnız müvəqqəti saxlanılır. «Təsdiqlə və saxla» seçilmədən şirkət bazasına yazılmır.",
      samples: []
    }
  };

  function selectScope(next) {
    if (busy || !screens[next] || (next === "transfer" && !canTransfer) ||
        (next === "stock-action" && !canStockActions) ||
        (next === "stock-xlsx" && !canXlsx)) return;
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
    ui.photoPanel.hidden = scope !== "photo";
    ui.transferPanel.hidden = scope !== "transfer";
    ui.actionPanel.hidden = scope !== "stock-action";
    ui.xlsxPanel.hidden = scope !== "stock-xlsx";
    ui.uploadPanel.hidden = scope !== "upload";
    if (scope === "upload" && active) void refreshUploadJobs();
    ui.composer.hidden = ["upload", "photo", "transfer", "stock-action", "stock-xlsx"].includes(scope);
    ui.suggestions.hidden = ["upload", "photo", "transfer", "stock-action", "stock-xlsx"].includes(scope);
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

  function photoMessage(message, error = false) {
    ui.photoFeedback.textContent = message;
    ui.photoFeedback.classList.toggle("error", error);
  }

  async function lookupPhoto() {
    if (!active || busy || scope !== "photo") return;
    const file = ui.photoFile.files?.[0];
    if (!file) { photoMessage("Əvvəlcə məhsul şəklini seçin.", true); return; }
    if (!["image/jpeg", "image/png"].includes(file.type) ||
        file.size < 1 || file.size > 4 * 1024 * 1024) {
      photoMessage("Yalnız 4 MB-dan kiçik JPG və ya PNG şəklini seçin.", true);
      return;
    }
    busy = true;
    ui.photoSearch.disabled = true;
    ui.photoResults.replaceChildren();
    photoMessage("Məhsul nümunələri və fayllardakı şəkillər yoxlanılır…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch("/api/photo/lookup", {
        method: "POST", cache: "no-store", credentials: "omit",
        headers: {
          "X-Telegram-Init-Data": signed,
          "Content-Type": file.type
        },
        body: file, signal: controller.signal
      });
      if (response.status === 401) {
        lock("Giriş müddəti bitib. Telegram botundan yenidən daxil olun.");
        return;
      }
      if (response.status === 400) throw new Error("Şəkil oxuna bilmədi. Başqa JPG/PNG foto seçin.");
      if (response.status === 413) throw new Error("Şəklin ölçüsü 4 MB-dan böyükdür.");
      if (response.status === 429) throw new Error("Çox sayda sorğu göndərilib. Bir qədər sonra sınayın.");
      if (!response.ok) throw new Error("Şəkil axtarışı mümkün olmadı. Telegram botundan istifadə edin.");
      const result = await response.json();
      if (!["exact", "candidates", "source_candidates", "not_found", "unavailable", "unconfirmed"].includes(result.status)) {
        throw new Error("Məhsul yoxlaması etibarlı nəticə qaytarmadı.");
      }
      if (result.status === "exact") {
        photoMessage("Təsdiqlənmiş eyni şəkil tapıldı.");
      } else if (result.status === "candidates") {
        photoMessage("Oxşar fotolar tapıldı. Məhsul kodunu mütləq yoxlayın.");
      } else if (result.status === "source_candidates") {
        photoMessage("Faylların daxilində oxşar şəkil mənbələri tapıldı.");
      } else {
        photoMessage("Təsdiqlənmiş məhsul müəyyən edilmədi.");
      }
      const notice = document.createElement("p");
      notice.textContent = result.notice || "Məhsulu kodla yoxlayın.";
      ui.photoResults.append(notice);
      if (result.incomplete) {
        const limit = document.createElement("p");
        limit.textContent = "Arxivin yalnız bir hissəsi yoxlanılıb.";
        ui.photoResults.append(limit);
      }
      for (const item of Array.isArray(result.sources) ? result.sources.slice(0, 3) : []) {
        if (typeof item !== "object" || item === null) continue;
        const name = typeof item.filename === "string" ? item.filename.slice(0, 96) : "";
        const kind = typeof item.type === "string" ? item.type.slice(0, 16) : "";
        const where = typeof item.location === "string" ? item.location.slice(0, 100) : "";
        if (!name) continue;
        const element = document.createElement("p");
        element.className = "candidate";
        element.textContent = name + " (" + kind + ") · " + where +
          " — sənəd şəkli, məhsul kimliyi təsdiqlənməyib";
        ui.photoResults.append(element);
      }
      for (const code of Array.isArray(result.codes) ? result.codes.slice(0, 3) : []) {
        if (typeof code !== "string" || code.length > 70) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "candidate";
        button.textContent = code + " — Kataloqda yoxla →";
        button.addEventListener("click", () => {
          selectScope("product");
          ui.question.value = code;
          ui.question.focus();
        });
        ui.photoResults.append(button);
      }
    } catch (error) {
      photoMessage(error?.message || "Foto yoxlanılmadı.", true);
    } finally {
      clearTimeout(timeout);
      busy = false;
      ui.photoSearch.disabled = !active;
    }
  }

  let webPhotoMatches = [];
  let webPhotoPage = 0;

  function renderWebPhotoPage() {
    ui.photoResults.replaceChildren();
    const total = webPhotoMatches.length;
    const pageSize = 10;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    photoMessage("İnternet şəkil namizədləri: " + total + " · " +
      (webPhotoPage + 1) + "/" + pages + " səhifə");
    for (const hit of webPhotoMatches.slice(webPhotoPage * pageSize, (webPhotoPage + 1) * pageSize)) {
      if (typeof hit !== "object" || hit === null) continue;
      const card = document.createElement("article");
      card.className = "candidate";
      if (typeof hit.thumbnail_url === "string" &&
          /^https:\/\//i.test(hit.thumbnail_url) && hit.thumbnail_url.length <= 2048) {
        const photo = document.createElement("img");
        photo.src = hit.thumbnail_url;
        photo.alt = "Mənbə şəklinin önizləməsi";
        photo.referrerPolicy = "no-referrer";
        photo.loading = "lazy";
        photo.style.maxWidth = "140px";
        photo.style.maxHeight = "140px";
        card.append(photo);
      }
      const title = document.createElement("p");
      title.textContent = (typeof hit.title === "string" ? hit.title.slice(0, 180) : "Şəkil") +
        (hit.priority ? " · PRIORİTET SAYT" : "") +
        " · " + (typeof hit.domain === "string" ? hit.domain.slice(0, 100) : "");
      card.append(title);
      for (const field of [["source_url", "Mənbə səhifəsini aç"], ["image_url", "Şəkli aç"]]) {
        const link = hit[field[0]];
        if (typeof link !== "string" || !/^https?:\/\//i.test(link) || link.length > 2048) continue;
        const anchor = document.createElement("a");
        anchor.href = link;
        anchor.textContent = field[1] + " ↗";
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.referrerPolicy = "no-referrer";
        card.append(anchor);
        card.append(document.createTextNode("  "));
      }
      ui.photoResults.append(card);
    }
    if (pages > 1) {
      const actions = document.createElement("div");
      actions.className = "upload-actions";
      for (const config of [
        {label: "⬅️ Əvvəlki", page: webPhotoPage - 1},
        {label: "Növbəti ➡️", page: webPhotoPage + 1}
      ]) {
        if (config.page < 0 || config.page >= pages) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "upload-secondary";
        button.textContent = config.label;
        button.addEventListener("click", () => {
          webPhotoPage = config.page;
          renderWebPhotoPage();
        });
        actions.append(button);
      }
      ui.photoResults.append(actions);
    }
    if (!total) {
      const message = document.createElement("p");
      message.textContent = "Təsdiqlənən şəkil-mənbə cütü aşkar edilmədi.";
      ui.photoResults.append(message);
    }
  }

  async function lookupWebPhoto() {
    if (!active || busy || scope !== "photo") return;
    const file = ui.photoFile.files?.[0];
    if (!file || !["image/jpeg", "image/png"].includes(file.type) ||
        file.size < 1 || file.size > 4 * 1024 * 1024) {
      photoMessage("4 MB-dan kiçik JPG/PNG şəkil seçin.", true);
      return;
    }
    busy = true;
    ui.photoSearch.disabled = true;
    ui.photoWebSearch.disabled = true;
    ui.photoResults.replaceChildren();
    photoMessage("Şəkil açıq razılığınızla internet axtarış provayderinə göndərilir…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch("/api/photo/web", {
        method: "POST", cache: "no-store", credentials: "omit",
        headers: {"X-Telegram-Init-Data": signed, "Content-Type": file.type},
        body: file, signal: controller.signal
      });
      if (!response.ok) throw new Error("İnternet şəkil axtarışı hazırda mümkün deyil.");
      const result = await response.json();
      webPhotoMatches = Array.isArray(result.results) ? result.results.slice(0, 60) : [];
      webPhotoPage = 0;
      renderWebPhotoPage();
      if (typeof result.provider_url === "string" &&
          /^https:\/\/yandex\.com\//i.test(result.provider_url)) {
        const anchor = document.createElement("a");
        anchor.href = result.provider_url;
        anchor.textContent = "🌐 Nəticələrə Yandex-də də bax ↗";
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.referrerPolicy = "no-referrer";
        ui.photoResults.append(anchor);
      }
      if (result.status === "unavailable") {
        photoMessage("Provayder müvəqqəti bloklanıb və ya cavab vermir. CAPTCHA keçilmir.", true);
      }
    } catch (error) {
      photoMessage(error?.message || "İnternet axtarışı mümkün olmadı.", true);
    } finally {
      clearTimeout(timeout);
      busy = false;
      ui.photoSearch.disabled = !active;
      ui.photoWebSearch.disabled = !active;
    }
  }

  ui.photoWebSearch.addEventListener("click", () => { void lookupWebPhoto(); });

  ui.photoSearch.addEventListener("click", () => { void lookupPhoto(); });

  const jobStages = {
    queued: "Emal növbəsində",
    reading: "Sənəd oxunur / OCR",
    indexing: "Axtarış bazasına əlavə olunur",
    ready: "Axtarış üçün hazırdır",
    review: "Yoxlama tələb olunur",
    failed: "Emal alınmadı"
  };

  function renderJobs(jobs) {
    ui.jobList.replaceChildren();
    if (!Array.isArray(jobs) || !jobs.length) {
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent = "Hələ təsdiqlənmiş sənəd tapılmadı.";
      ui.jobList.append(message);
      return;
    }
    for (const job of jobs) {
      if (!Number.isInteger(job.job_id) || typeof job.stage !== "string") continue;
      const card = document.createElement("article");
      card.className = "job-card";
      const title = document.createElement("strong");
      title.textContent = typeof job.filename === "string" ? job.filename : "Sənəd";
      const status = document.createElement("span");
      status.className = "job-stage " + (
        job.stage === "ready" ? "completed" :
        ["failed", "review"].includes(job.stage) ? "requires-review" : ""
      );
      status.textContent = jobStages[job.stage] || "Vəziyyət məlum deyil";
      const note = document.createElement("p");
      note.textContent = typeof job.notice === "string" ? job.notice : "";
      const detail = document.createElement("small");
      detail.textContent = "Emal №" + job.job_id;
      card.append(title, status, note, detail);
      ui.jobList.append(card);
    }
  }

  async function refreshUploadJobs() {
    if (!active || ui.uploadPanel.hidden) return;
    ui.jobsRefresh.disabled = true;
    try {
      const result = await request("/api/uploads/recent", {});
      renderJobs(result.jobs);
    } catch (error) {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = error?.message || "Vəziyyəti yeniləmək mümkün olmadı.";
      ui.jobList.replaceChildren(note);
    } finally {
      ui.jobsRefresh.disabled = !active;
    }
  }

  ui.jobsRefresh.addEventListener("click", () => { void refreshUploadJobs(); });

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
          (result.duplicate
            ? "Eyni faylın baytları əvvəl saxlanılıb; yeni orijinal nüsxə yaradılmadı. "
            : "Sənəd qəbul edildi. ") +
          "Emal işi №" + result.job_id +
          " yaradıldı. Sənədin oxunması və axtarışa daxil edilməsi ayrıca davam edir.",
          "assistant"
        );
        await refreshUploadJobs();
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
          ? [
              stock.product_code,
              "Vahid: " + (stock.unit || "təsdiqlənməyib"),
              ...stock.locations.map(x => x.warehouse + ": " + x.quantity),
              ...(stock.total === null ? [] : ["Təsdiqlənmiş anbarlar üzrə cəm: " + stock.total])
            ]
          : [stock.product_code, "Qalıq MƏLUM DEYİL"];
        pending.textContent = lines.join("\n") + "\n\n" + stock.notice;
        if (stock.known) {
          const historyButton = document.createElement("button");
          historyButton.type = "button";
          historyButton.className = "candidate";
          historyButton.textContent = "Son anbar hərəkətlərini göstər →";
          historyButton.addEventListener("click", async () => {
            if (!active || historyButton.disabled) return;
            historyButton.disabled = true;
            try {
              const history = await request("/api/stock/history", {code: stock.product_code});
              const rows = Array.isArray(history.items) ? history.items.slice(0, 12) : [];
              const summary = rows.map(x => [
                x.warehouse + " · " + x.kind,
                x.quantity + " · Qalıq: " + x.balance_after,
                "Sənəd: " + x.reference,
                x.confirmed_at ? new Date(x.confirmed_at).toLocaleString("az-AZ") : ""
              ].join(" — "));
              appendBubble([history.notice || "Anbar tarixçəsi", ...summary].join("\n"), "assistant");
            } catch (error) {
              if (active) appendBubble(error?.message || "Tarixçə açıla bilmədi.", "assistant error");
            } finally {
              historyButton.disabled = !active;
            }
          });
          pending.append(historyButton);
        }
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

  function transferValue(id) {
    return $(id).value.trim();
  }

  function transferState(message, failure = false) {
    ui.transferFeedback.textContent = message;
    ui.transferFeedback.classList.toggle("error", failure);
  }

  function clearTransferPreview() {
    transferTicket = null;
    ui.transferPreview.hidden = true;
    ui.transferDetails.textContent = "";
  }

  async function previewTransfer() {
    if (!active || !canTransfer || transferBusy) return;
    transferBusy = true;
    ui.transferPrepare.disabled = true;
    clearTransferPreview();
    transferState("Anbar və məhsul məlumatları yoxlanılır...");
    try {
      const unit = transferValue("transfer-unit");
      const result = await request("/api/stock/transfer/preview", {
        code: transferValue("transfer-code"),
        source: transferValue("transfer-source"),
        destination: transferValue("transfer-destination"),
        quantity: transferValue("transfer-quantity"),
        reference: transferValue("transfer-reference"),
        unit: unit || null
      });
      if (!active) return;
      transferTicket = result.token;
      ui.transferDetails.textContent = [
        "Məhsul: " + result.product_code,
        "Mənbə: " + result.source + " → " + result.source_after_preview,
        "Təyinat: " + result.destination + " → " + result.destination_after_preview,
        "Miqdar: " + result.quantity + " " + (result.unit || "(vahid təsdiqlənməyib)"),
        "Sənəd: " + result.reference,
        result.warning
      ].join("\n");
      ui.transferPreview.hidden = false;
      transferState("Önbaxış hazırdır. Təsdiq müddəti 2 dəqiqədir.");
    } catch (error) {
      if (active) transferState(error?.message || "Önbaxış alınmadı.", true);
    } finally {
      transferBusy = false;
      ui.transferPrepare.disabled = !active;
    }
  }

  async function finishTransfer(confirm) {
    if (!active || !canTransfer || transferBusy || !transferTicket) return;
    transferBusy = true;
    ui.transferConfirm.disabled = true;
    ui.transferCancel.disabled = true;
    const token = transferTicket;
    clearTransferPreview();
    transferState(confirm ? "Köçürmə təsdiqlənir..." : "Təsdiq ləğv edilir...");
    try {
      const result = await request(
        confirm ? "/api/stock/transfer/confirm" : "/api/stock/transfer/cancel",
        {token}
      );
      if (!active) return;
      if (confirm) {
        transferState("Köçürmə təsdiqləndi. Sənəd: " + result.reference);
        appendBubble([
          "ANBAR KÖÇÜRMƏSİ TƏSDİQLƏNDİ",
          "Məhsul: " + result.product_code,
          "Mənbə: " + result.source + " · qalıq: " + result.source_balance_after,
          "Təyinat: " + result.destination + " · qalıq: " + result.destination_balance_after,
          "Sənəd: " + result.reference
        ].join("\n"), "assistant");
      } else {
        transferState("Önbaxış ləğv edildi; anbar qalığı dəyişdirilmədi.");
      }
    } catch (error) {
      if (active) transferState(
        (error?.message || "Təsdiq alınmadı.") +
        " Təkrar göndərməzdən əvvəl anbar tarixçəsini yoxlayın.", true
      );
    } finally {
      transferBusy = false;
      ui.transferConfirm.disabled = false;
      ui.transferCancel.disabled = false;
    }
  }

  function actionState(message, failure = false) {
    ui.actionFeedback.textContent = message;
    ui.actionFeedback.classList.toggle("error", failure);
  }
  function clearActionPreview() {
    actionTicket = null;
    ui.actionPreview.hidden = true;
    ui.actionDetails.textContent = "";
  }
  function updateActionFields() {
    const kind = ui.actionKind.value;
    const finalizing = kind === "RESERVATION_CANCEL" || kind === "RESERVATION_CONSUME";
    for (const id of ["stock-action-warehouse", "stock-action-quantity", "stock-action-unit"]) {
      const field = $(id);
      field.closest("label").hidden = finalizing;
      field.disabled = finalizing;
    }
    ui.actionReasonField.hidden = finalizing || kind === "RESERVE";
    ui.actionPrepare.textContent = finalizing
      ? "Rezervasiyanı önbaxışda yoxla →"
      : "Əməliyyatı önbaxışda yoxla →";
    clearActionPreview();
  }
  ui.actionKind.addEventListener("change", updateActionFields);
  updateActionFields();
  for (const field of ui.actionPanel.querySelectorAll("input, textarea, select")) {
    field.addEventListener("input", clearActionPreview);
  }
  async function previewStockAction() {
    if (!active || !canStockActions || actionBusy || actionTicket) return;
    actionBusy = true;
    ui.actionPrepare.disabled = true;
    clearActionPreview();
    actionState("Əməliyyatın önbaxışı yoxlanılır...");
    try {
      const kind = ui.actionKind.value;
      const finalizing = kind === "RESERVATION_CANCEL" || kind === "RESERVATION_CONSUME";
      const unit = $("stock-action-unit").value.trim();
      const result = await request("/api/stock/action/preview", {
        kind,
        code: $("stock-action-code").value.trim(),
        warehouse: finalizing ? "" : $("stock-action-warehouse").value.trim(),
        quantity: finalizing ? "" : $("stock-action-quantity").value.trim(),
        reference: $("stock-action-reference").value.trim(),
        unit: finalizing ? null : (unit || null),
        reason: finalizing || kind === "RESERVE" ? null : $("stock-action-reason").value.trim()
      });
      if (!active) return;
      actionTicket = result.token;
      ui.actionDetails.textContent = [
        "Əməliyyat: " + result.kind,
        "Məhsul: " + result.product_code,
        "Anbar: " + result.warehouse,
        "Miqdar: " + result.quantity + " " + (result.unit || "(vahid təsdiqsiz)"),
        "Sənəd: " + result.reference,
        "Əvvəlki fiziki qalıq: " + result.on_hand_before,
        "Təxmini sonrakı fiziki qalıq: " + result.on_hand_after_if_approved,
        ...(result.reserved_before === undefined ? [] : [
          "Hazırkı rezerv: " + result.reserved_before,
          "Hazırkı sərbəst stok: " + result.available_before
        ]),
        result.warning
      ].join("\n");
      ui.actionPreview.hidden = false;
      actionState("Önbaxış hazırdır. Təsdiq 2 dəqiqə ərzində verilməlidir.");
    } catch (error) {
      if (active) actionState(error?.message || "Əməliyyat yoxlanmadı.", true);
    } finally {
      actionBusy = false;
      ui.actionPrepare.disabled = !active;
    }
  }
  async function finishStockAction(confirm) {
    if (!active || !canStockActions || actionBusy || !actionTicket) return;
    actionBusy = true;
    ui.actionConfirm.disabled = true;
    ui.actionCancel.disabled = true;
    const token = actionTicket;
    clearActionPreview();
    actionState(confirm ? "Təsdiq edilir..." : "Ləğv edilir...");
    try {
      const result = await request(
        confirm ? "/api/stock/action/confirm" : "/api/stock/action/cancel", {token}
      );
      if (!active) return;
      if (confirm) {
        actionState("Əməliyyat qeydə alındı. Sənəd: " + result.reference);
        appendBubble(
          "ANBAR ƏMƏLİYYATI QEYDƏ ALINDI\n" +
          "Məhsul: " + result.product_code + "\n" +
          "Növ: " + result.kind + "\n" +
          "Sənəd: " + result.reference + "\n" +
          (result.available === undefined
            ? "Yeni qalıq: " + result.balance_after
            : "Fiziki qalıq: " + result.on_hand +
              " · Rezerv: " + result.reserved + " · Sərbəst: " + result.available),
          "assistant"
        );
      } else {
        actionState("Təsdiq ləğv edildi, stok dəyişdirilmədi.");
      }
    } catch (error) {
      if (active) actionState(
        (error?.message || "Təsdiq mümkün olmadı.") +
        " Yenidən təsdiq etməzdən əvvəl stok tarixçəsini yoxlayın.", true
      );
    } finally {
      actionBusy = false;
      ui.actionConfirm.disabled = false;
      ui.actionCancel.disabled = false;
    }
  }
  function xlsxState(message, failure = false) {
    ui.xlsxFeedback.textContent = message;
    ui.xlsxFeedback.classList.toggle("error", failure);
  }
  function clearXlsxPreview() {
    xlsxTicket = null;
    ui.xlsxPreview.hidden = true;
    ui.xlsxRows.replaceChildren();
    ui.xlsxFile.disabled = false;
  }
  async function previewXlsx() {
    if (!active || !canXlsx || xlsxBusy || xlsxTicket) return;
    const file = ui.xlsxFile.files?.[0];
    if (!file || !file.name.toLowerCase().endsWith(".xlsx") ||
        file.size < 1 || file.size > 5 * 1024 * 1024) {
      xlsxState("Bir .xlsx seçin (maksimum 5 MB).", true);
      return;
    }
    xlsxBusy = true;
    ui.xlsxPrepare.disabled = true;
    clearXlsxPreview();
    xlsxState("Excel yalnız təhlükəsiz önbaxış üçün oxunur...");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch("/api/stock/xlsx/preview", {
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        headers: {
          "X-Telegram-Init-Data": signed,
          "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
        body: file,
        signal: controller.signal
      });
      if (response.status === 401) {
        lock("Excel idxalı üçün administrator girişi lazımdır.");
        throw new Error("Administrator icazəsi yoxdur.");
      }
      if (!response.ok) {
        throw new Error(response.status === 413
          ? "Excel 5 MB limitini aşır."
          : "Excel yoxlanmadı. Şablonu, düsturları və sətirləri yenidən yoxlayın.");
      }
      const result = await response.json();
      if (!active) return;
      xlsxTicket = result.token;
      ui.xlsxFile.disabled = true;
      ui.xlsxSummary.textContent = "Sətir sayı: " + result.rows +
        " · Açılış: " + result.openings +
        " · Giriş: " + result.receipts +
        " · Çıxış: " + result.issues;
      ui.xlsxDigest.textContent = "Fayl SHA-256: " + result.sha256;
      for (const item of result.items) {
        const line = document.createElement("div");
        line.className = "xlsx-row";
        line.textContent = [
          item.code, item.warehouse, item.kind,
          item.quantity, item.unit || "vahid təsdiqsiz", item.reference
        ].join(" | ");
        ui.xlsxRows.append(line);
      }
      ui.xlsxPreview.hidden = false;
      xlsxState("Önbaxış hazırdır. 2 dəqiqə ərzində bütün sətrləri yoxlayın.");
    } catch (error) {
      if (active) xlsxState(error?.message || "Önbaxış alınmadı.", true);
    } finally {
      clearTimeout(timeout);
      xlsxBusy = false;
      ui.xlsxPrepare.disabled = !active;
    }
  }
  async function finishXlsx(confirm) {
    if (!active || !canXlsx || xlsxBusy || !xlsxTicket) return;
    xlsxBusy = true;
    ui.xlsxConfirm.disabled = true;
    ui.xlsxCancel.disabled = true;
    const token = xlsxTicket;
    clearXlsxPreview();
    xlsxState(confirm ? "Bütün sətirlər atomik yazılır..." : "Ləğv edilir...");
    try {
      const result = await request(
        confirm ? "/api/stock/xlsx/confirm" : "/api/stock/xlsx/cancel", {token}
      );
      if (!active) return;
      xlsxState(confirm
        ? result.count + " stok sətri təsdiqləndi. Faylın orijinalını məxfi arxivdə saxlayın."
        : "Önbaxış ləğv edildi. Stok dəyişdirilmədi.");
      ui.xlsxFile.value = "";
    } catch (error) {
      if (active) xlsxState(
        (error?.message || "İdxal başa çatmadı.") +
        " Təkrar idxaldan əvvəl hərəkət tarixçəsini yoxlayın.", true
      );
    } finally {
      xlsxBusy = false;
      ui.xlsxConfirm.disabled = false;
      ui.xlsxCancel.disabled = false;
    }
  }
  ui.xlsxPrepare.addEventListener("click", () => { void previewXlsx(); });
  ui.xlsxConfirm.addEventListener("click", () => { void finishXlsx(true); });
  ui.xlsxCancel.addEventListener("click", () => { void finishXlsx(false); });

  ui.actionPrepare.addEventListener("click", () => { void previewStockAction(); });
  ui.actionConfirm.addEventListener("click", () => { void finishStockAction(true); });
  ui.actionCancel.addEventListener("click", () => { void finishStockAction(false); });

  ui.transferPrepare.addEventListener("click", () => { void previewTransfer(); });
  ui.transferConfirm.addEventListener("click", () => { void finishTransfer(true); });
  ui.transferCancel.addEventListener("click", () => { void finishTransfer(false); });

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
    canTransfer = profile.role === "admin" && profile.can_transfer === true;
    ui.transferMode.hidden = !canTransfer;
    canStockActions = profile.role === "admin" && profile.can_stock_actions === true;
    ui.actionMode.hidden = !canStockActions;
    canXlsx = profile.role === "admin" && profile.can_stock_xlsx === true;
    ui.xlsxMode.hidden = !canXlsx;
    ui.connection.textContent = "Təhlükəsiz bağlantı";
    ui.question.focus();
  }).catch(() => {
    if (!ui.locked.hidden) return;
    lock("Serverə təhlükəsiz qoşulmaq mümkün olmadı. Telegram botu mövcud söhbət üçün işləməyə davam edir.");
  });
})();
