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
    salesFields: $("sales-fields"), salesYear: $("sales-year")
  };
  let scope = "chat";
  let busy = false;
  let active = false;
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
        throw new Error("Xidmət hazırda əlçatan deyil. Telegram botunda sual verməyə davam edə bilərsiniz.");
      }
      return result;
    } finally {
      clearTimeout(timeout);
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
    }
  };

  function selectScope(next) {
    if (busy || !screens[next]) return;
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
