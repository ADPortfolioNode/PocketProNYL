window.PocketProQuery = (function () {
  const PHOTO = {
    take5: "css/assets/nyl-bg/take5.jpg",
    pick3: "css/assets/nyl-bg/training.jpg",
    powerball: "css/assets/nyl-bg/jackpot.jpg",
    general: "css/assets/nyl-bg/concierge.jpg",
  };
  const SAMPLES = {
    "What's hot in Take 5 lately?": {
      imageHint: "take5",
      answerHtml:
        "<p>Take 5 is a daily 5-of-39 draw. After ingest finishes, Concierge can cite recent frequency, gaps, and the latest trained suggestion.</p><p>When the Take 5 model is trained, the dashboard marks it <strong>up to date</strong> and suggestions appear in Games Ready for Experiments.</p>",
      citations: [{ url: "#take5", title: "Take 5 draw history", snippet: "Official NYL feed via Socrata." }],
    },
    "How do I train Pick 3 without a gateway timeout?": {
      imageHint: "pick3",
      answerHtml:
        "<p>Training is a background job. The UI posts <code>/api/train</code> and polls <code>/api/train_status</code> — do not wait on a ten-minute request.</p><p>Start with Auto-tune off and Max Iterations around 10, then check Completed Training Experiments.</p>",
      citations: [{ url: "#train", title: "Training status poll", snippet: "GET /api/train_status" }],
    },
    "Which games are ready for suggestions?": {
      imageHint: "powerball",
      answerHtml:
        "<p>A game is ready when ingest is complete and a trained experiment exists. Powerball, Mega Millions, Pick 10, Cash4Life, and Quick Draw typically appear in <strong>Games Ready for Experiments</strong> after the first successful train.</p>",
      citations: [{ url: "#experiments", title: "Dashboard experiments", snippet: "Completed training experiments list." }],
    },
  };

  function lookupSample(question) {
    return (
      SAMPLES[question] || {
        imageHint: "general",
        answerHtml:
          "<p>Hi — I'm PocketPro Concierge. Ask about draws, training, suggestions, or errors. In the Docker app, toggle RAG to ground answers in Chroma history.</p>",
        citations: [],
      }
    );
  }

  function applyStagePhoto(hint) {
    const img = document.querySelector(".content-stage__photo");
    if (img) img.src = PHOTO[hint] || PHOTO.general;
  }

  function renderQueryResult(root, payload, question) {
    const data = lookupSample(question);
    applyStagePhoto(data.imageHint);
    const cites = (data.citations || [])
      .map(
        (c) =>
          `<li><span><span class="ref-title">${c.title}</span><a href="${c.url}">${c.url}</a></span></li>`,
      )
      .join("");
    root.innerHTML = `<p class="user-turn"><strong>You asked</strong> — ${question}</p>
      <article class="chat-magazine">${data.answerHtml}</article>
      <section class="chat-meta" aria-label="References"><h3>References</h3>
      <ol class="chat-meta-links">${cites}</ol></section>`;
  }

  return { lookupSample, renderQueryResult, applyStagePhoto };
})();
