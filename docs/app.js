"use strict";

const REPOSITORY = "serenwoon/g2b-daily";
const SNAPSHOT_API = `https://api.github.com/repos/${REPOSITORY}/contents/snapshots?ref=main`;
const PAGE_SIZE = 80;

const state = {
  allRows: [],
  filteredRows: [],
  visibleCount: PAGE_SIZE,
  snapshotName: "",
  files: [],
};

const elements = {
  snapshotMeta: document.querySelector("#snapshot-meta"),
  metricTotal: document.querySelector("#metric-total"),
  metricRegistered: document.querySelector("#metric-registered"),
  metricChanged: document.querySelector("#metric-changed"),
  metricCancelled: document.querySelector("#metric-cancelled"),
  metricReissued: document.querySelector("#metric-reissued"),
  trendChart: document.querySelector("#trend-chart"),
  searchInput: document.querySelector("#search-input"),
  statusFilter: document.querySelector("#status-filter"),
  sortOrder: document.querySelector("#sort-order"),
  fileInput: document.querySelector("#file-input"),
  resultCount: document.querySelector("#result-count"),
  noticeList: document.querySelector("#notice-list"),
  loadMore: document.querySelector("#load-more"),
  errorPanel: document.querySelector("#error-panel"),
  errorMessage: document.querySelector("#error-message"),
  retryButton: document.querySelector("#retry-button"),
};

const numberFormatter = new Intl.NumberFormat("ko-KR");

function parseJsonLines(text) {
  if (!text.trim()) return [];
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`${index + 1}번째 줄의 JSON 형식이 올바르지 않습니다.`);
      }
    });
}

function countStatus(rows, status) {
  return rows.filter((row) => row.ntceKindNm === status).length;
}

function formatNumber(value) {
  return numberFormatter.format(value ?? 0);
}

function formatWon(value) {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (numeric >= 100_000_000) return `${(numeric / 100_000_000).toFixed(numeric >= 1_000_000_000 ? 0 : 1)}억원`;
  if (numeric >= 10_000) return `${Math.round(numeric / 10_000)}만원`;
  return `${numberFormatter.format(numeric)}원`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  return match ? `${match[2]}.${match[3]} ${match[4]}:${match[5]}` : String(value);
}

function safeText(value, fallback = "—") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function updateSummary(rows, sourceLabel) {
  elements.metricTotal.textContent = formatNumber(rows.length);
  elements.metricRegistered.textContent = formatNumber(countStatus(rows, "등록공고"));
  elements.metricChanged.textContent = formatNumber(countStatus(rows, "변경공고"));
  elements.metricCancelled.textContent = formatNumber(countStatus(rows, "취소공고"));
  elements.metricReissued.textContent = formatNumber(countStatus(rows, "재공고"));

  const snapshotDate = rows[0]?.snapshotDate || state.snapshotName.replace(".jsonl", "") || "날짜 미상";
  elements.snapshotMeta.textContent = `${snapshotDate} 스냅샷 · ${formatNumber(rows.length)}건 · ${sourceLabel}`;
}

function populateStatuses(rows) {
  const current = elements.statusFilter.value;
  const statuses = [...new Set(rows.map((row) => row.ntceKindNm).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ko"));
  elements.statusFilter.replaceChildren(new Option("전체 상태", ""));
  for (const status of statuses) elements.statusFilter.add(new Option(status, status));
  if (statuses.includes(current)) elements.statusFilter.value = current;
}

function rowSearchText(row) {
  return [row.bidNtceNm, row.ntceInsttNm, row.dminsttNm, row.bidNtceNo]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("ko");
}

function compareRows(a, b, sortOrder) {
  if (sortOrder === "deadline") {
    return safeText(a.bidClseDt, "9999").localeCompare(safeText(b.bidClseDt, "9999"));
  }
  if (sortOrder === "name") {
    return safeText(a.bidNtceNm, "").localeCompare(safeText(b.bidNtceNm, ""), "ko");
  }
  const aDate = a.rgstDt || a.bidNtceDt || "";
  const bDate = b.rgstDt || b.bidNtceDt || "";
  return bDate.localeCompare(aDate);
}

function applyControls() {
  const query = elements.searchInput.value.trim().toLocaleLowerCase("ko");
  const status = elements.statusFilter.value;
  const sortOrder = elements.sortOrder.value;

  state.filteredRows = state.allRows
    .filter((row) => (!status || row.ntceKindNm === status) && (!query || rowSearchText(row).includes(query)))
    .sort((a, b) => compareRows(a, b, sortOrder));
  state.visibleCount = PAGE_SIZE;
  renderRows();
}

function makeTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function createNoticeRow(row) {
  const article = document.createElement("article");
  article.className = "notice-row";

  const status = makeTextElement("span", "status-badge", safeText(row.ntceKindNm, "상태 없음"));
  status.dataset.status = safeText(row.ntceKindNm, "");
  article.append(status);

  const main = document.createElement("div");
  main.className = "notice-main";
  main.append(makeTextElement("h3", "notice-title", safeText(row.bidNtceNm, "제목 없음")));
  const sub = document.createElement("div");
  sub.className = "notice-sub";
  sub.append(
    makeTextElement("span", "", safeText(row.bidNtceNo, "공고번호 없음")),
    makeTextElement("span", "", safeText(row.ntceInsttNm || row.dminsttNm, "기관 정보 없음")),
  );
  main.append(sub);
  article.append(main);

  const deadline = document.createElement("div");
  deadline.className = "notice-cell";
  deadline.append(makeTextElement("span", "", "입찰 마감"), makeTextElement("strong", "", formatDateTime(row.bidClseDt)));
  article.append(deadline);

  const budget = document.createElement("div");
  budget.className = "notice-cell";
  budget.append(makeTextElement("span", "", "배정예산"), makeTextElement("strong", "", formatWon(row.asignBdgtAmt)));
  article.append(budget);

  return article;
}

function renderRows() {
  const visibleRows = state.filteredRows.slice(0, state.visibleCount);
  const fragment = document.createDocumentFragment();
  for (const row of visibleRows) fragment.append(createNoticeRow(row));
  elements.noticeList.replaceChildren(fragment);

  if (!visibleRows.length) {
    elements.noticeList.append(makeTextElement("div", "empty-card", "조건에 맞는 공고가 없습니다."));
  }

  elements.resultCount.textContent = `전체 ${formatNumber(state.allRows.length)}건 중 ${formatNumber(state.filteredRows.length)}건`;
  elements.loadMore.hidden = state.visibleCount >= state.filteredRows.length;
}

function renderTrend(points) {
  if (!points.length) {
    elements.trendChart.replaceChildren(makeTextElement("span", "muted", "표시할 스냅샷이 없습니다."));
    return;
  }

  const maxCount = Math.max(...points.map((point) => point.count), 1);
  const fragment = document.createDocumentFragment();
  points.forEach((point, index) => {
    const wrap = document.createElement("div");
    wrap.className = "trend-bar-wrap";
    const bar = document.createElement("div");
    bar.className = "trend-bar";
    bar.style.height = `${Math.max(4, (point.count / maxCount) * 94)}px`;
    bar.title = `${point.date}: ${formatNumber(point.count)}건`;
    const label = makeTextElement("span", "trend-date", index === 0 || index === points.length - 1 ? point.date.slice(5) : "");
    wrap.append(bar, label);
    fragment.append(wrap);
  });
  elements.trendChart.replaceChildren(fragment);
  elements.trendChart.setAttribute("aria-label", `${points[0].date}부터 ${points.at(-1).date}까지 날짜별 공고 건수`);
}

async function loadTrend(files) {
  const recentFiles = files.slice(-30);
  const results = await Promise.allSettled(
    recentFiles.map(async (file) => {
      const response = await fetch(file.download_url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const text = await response.text();
      return { date: file.name.slice(0, 10), count: parseJsonLines(text).length };
    }),
  );
  renderTrend(results.filter((result) => result.status === "fulfilled").map((result) => result.value));
}

function useRows(rows, sourceLabel) {
  state.allRows = rows;
  state.filteredRows = [...rows];
  elements.errorPanel.hidden = true;
  updateSummary(rows, sourceLabel);
  populateStatuses(rows);
  applyControls();
}

async function loadLatest() {
  elements.errorPanel.hidden = true;
  elements.snapshotMeta.textContent = "최신 스냅샷을 불러오는 중입니다";
  elements.noticeList.replaceChildren(makeTextElement("div", "loading-card", "최신 공고를 정리하고 있습니다…"));

  try {
    const response = await fetch(SNAPSHOT_API, { headers: { Accept: "application/vnd.github+json" }, cache: "no-store" });
    if (!response.ok) throw new Error(`GitHub API 응답 ${response.status}`);
    const entries = await response.json();
    const files = entries
      .filter((entry) => entry.type === "file" && /^\d{4}-\d{2}-\d{2}\.jsonl$/.test(entry.name))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (!files.length) throw new Error("저장된 스냅샷을 찾지 못했습니다.");

    const latest = files.at(-1);
    const snapshotResponse = await fetch(latest.download_url, { cache: "no-store" });
    if (!snapshotResponse.ok) throw new Error(`스냅샷 응답 ${snapshotResponse.status}`);

    state.files = files;
    state.snapshotName = latest.name;
    useRows(parseJsonLines(await snapshotResponse.text()), "GitHub 공개 데이터");
    loadTrend(files).catch(() => renderTrend([]));
  } catch (error) {
    elements.errorPanel.hidden = false;
    elements.errorMessage.textContent = error.message;
    elements.snapshotMeta.textContent = "공개 데이터를 불러오지 못했습니다";
    elements.noticeList.replaceChildren(makeTextElement("div", "empty-card", "JSONL 직접 열기를 이용할 수 있습니다."));
  }
}

elements.searchInput.addEventListener("input", applyControls);
elements.statusFilter.addEventListener("change", applyControls);
elements.sortOrder.addEventListener("change", applyControls);
elements.retryButton.addEventListener("click", loadLatest);
elements.loadMore.addEventListener("click", () => {
  state.visibleCount += PAGE_SIZE;
  renderRows();
});
elements.fileInput.addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    state.snapshotName = file.name;
    useRows(parseJsonLines(await file.text()), "브라우저에서 연 로컬 파일");
    renderTrend([{ date: file.name.slice(0, 10), count: state.allRows.length }]);
  } catch (error) {
    elements.errorPanel.hidden = false;
    elements.errorMessage.textContent = error.message;
  }
});

loadLatest();
