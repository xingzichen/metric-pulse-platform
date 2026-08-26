<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute } from "vue-router";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, post } from "../../../api";
import type { Unit } from "../../../types";
import StatusTag from "../../../components/StatusTag.vue";
import { getReasonLabel, getStatusPresentation } from "../../../status-display";

// 审核台按“队列 + 当前单元上下文”分层加载，切换行时无需重复下载整批证据。
const id = String(useRoute<"/tasks/[taskId]/review">().params.taskId);
const qc = useQueryClient();
const filter = ref("UNREVIEWED");
const executionFilter = ref("ALL");
const page = ref(1);
const selected = ref("");
const queue = useQuery({
  queryKey: ["review-queue", id, filter, executionFilter, page],
  queryFn: () =>
    api<{ items: Unit[]; total: number }>(
      `/api/v1/tasks/${id}/review-queue?reviewStatus=${filter.value}${executionFilter.value === "ALL" ? "" : `&executionStatus=${executionFilter.value}`}&offset=${(page.value - 1) * 50}&limit=50`,
    ),
});
watch([filter, executionFilter], () => {
  page.value = 1;
  selectedIds.value = [];
});
watch(
  () => queue.data.value?.items,
  (items) => {
    // 翻页或筛选后若原选择已离开队列，自动定位第一条，避免右侧展示陈旧记录。
    if (items?.length && !items.some((x: Unit) => x.id === selected.value))
      selected.value = items[0].id;
  },
  { immediate: true },
);
const context = useQuery({
  queryKey: ["unit-context", selected],
  enabled: computed(() => !!selected.value),
  queryFn: () => api<Unit>(`/api/v1/review-units/${selected.value}`),
});
const corrected = reactive<Record<string, unknown>>({});
const comment = ref("");
const selectedIds = ref<string[]>([]);
const fieldLabels: Record<string, string> = {
  index_name: "指标名称",
  level: "级别",
  region: "区域",
  province: "省份",
  city: "城市",
  district: "区县",
  other_region: "经济区",
  statistical_date: "统计时间",
  scope: "更新频次",
  industry: "应用产业",
  be_data: "来源原始值",
  be_unit: "来源原始单位",
  data: "标准值（程序换算）",
  unit: "标准单位",
  source_url: "采集来源链接",
  logic_id: "数据标识",
  collect_date: "采集时间",
  rank: "当前排名",
  name: "项目名称",
  star: "收藏量",
  star_unit: "收藏量单位",
  source_department: "来源平台",
  update_frequency: "更新频次",
  datasource_date: "数据来源时间",
  collection_date: "数据入库时间",
  data_type: "数据类型",
  data_status: "数据状态",
  rank_year: "榜单年度",
  company_name: "公司",
  headquarter_location: "总部所在地",
  CEO: "首席执行官",
  financing_amount: "筹资金额",
  financing_amount_unit: "筹资金额单位",
  establish_date: "成立时间",
  source: "来源机构",
};
const fieldLabel = (field: string) => fieldLabels[field] || field;
const rowContract = computed(
  () => context.data.value?.record?.rowContract || {},
);
const isAiIndex = computed(() => rowContract.value.profile === "ai_index_v1");
const isAlgorithmCollection = computed(
  () => rowContract.value.profile === "ai_algorithm_collection_monthly_v1",
);
const isForbesAi50 = computed(
  () => rowContract.value.profile === "top_list_ai_forbes_annual_v1",
);
const isExecutionFailure = computed(
  () => context.data.value?.executionStatus === "FAILED_FINAL",
);
const usesSearchFallback = computed(() =>
  (context.data.value?.acquisitionAttempts || []).some(
    (attempt) => attempt.route === "SEARCH_FALLBACK",
  ),
);
const algorithmApplicationFields = new Set([
  "logic_id",
  "collect_date",
  "rank",
  "star_unit",
  "source_department",
  "source_url",
  "update_frequency",
  "datasource_date",
  "collection_date",
  "data_type",
  "data_status",
]);
const algorithmAudit = computed<Record<string, unknown>>(() => {
  const values = context.data.value?.validation?.deterministic_profile_values;
  return values && typeof values === "object"
    ? (values as Record<string, unknown>)
    : {};
});
const forbesApplicationFields = new Set([
  "logic_id",
  "rank_year",
  "financing_amount_unit",
  "source",
  "source_url",
  "update_frequency",
  "datasource_date",
  "collection_date",
  "data_type",
  "data_status",
]);
const forbesAudit = computed<Record<string, unknown>>(() => {
  const values = context.data.value?.validation?.deterministic_profile_values;
  return values && typeof values === "object"
    ? (values as Record<string, unknown>)
    : {};
});
const conversionAudit = computed<Record<string, unknown> | null>(() => {
  const conversion = context.data.value?.validation?.conversion;
  return conversion && typeof conversion === "object"
    ? (conversion as Record<string, unknown>)
    : null;
});
const conversionInputsChanged = computed(() => {
  if (!conversionAudit.value) return false;
  const originalValue = conversionAudit.value.source_value;
  const originalUnit = conversionAudit.value.source_unit;
  return (
    String(corrected.be_data ?? "") !== String(originalValue ?? "") ||
    String(corrected.be_unit ?? "") !== String(originalUnit ?? "")
  );
});
const constraintAudit = computed(() => {
  const required = Array.isArray(rowContract.value.required_matches)
    ? (rowContract.value.required_matches as string[])
    : [];
  const matches = context.data.value?.validation?.constraint_matches;
  const matchMap =
    matches && typeof matches === "object"
      ? (matches as Record<string, unknown>)
      : {};
  const descriptors =
    rowContract.value.descriptors &&
    typeof rowContract.value.descriptors === "object"
      ? (rowContract.value.descriptors as Record<string, unknown>)
      : {};
  return required.map((field) => ({
    field,
    value: descriptors[field],
    matched: matchMap[field] === true,
  }));
});
const evidenceSourceOptions = computed(() => {
  // 同一网址可能对应多段证据；选择框按 URL 去重并优先展示模型最终采用的来源。
  const items = context.data.value?.evidence || [];
  return Array.from(
    new Map(
      items
        .filter((item) => item.sourceUrl)
        .sort(
          (a, b) =>
            Number(b.metadata?.selected === true) -
            Number(a.metadata?.selected === true),
        )
        .map((item) => [item.sourceUrl!, item]),
    ).values(),
  );
});
const reviewFilterOptions = [
  "UNREVIEWED",
  "AUTO_APPROVED",
  "APPROVED",
  "CORRECTED",
  "CONFIRMED_UNRESOLVED",
  "REJECTED",
  "SKIPPED",
];
const executionFilterOptions = ["ALL", "FAILED_FINAL", "SUCCEEDED"];
const executionFilterLabel = (value: string) =>
  value === "ALL" ? "全部执行结果" : getStatusPresentation(value).label;
const canBulkApprove = (unit: Unit) => unit.executionStatus === "SUCCEEDED";
watch(
  () => context.data.value,
  (data) => {
    // 切换行时必须先清空 reactive 对象，否则上一行特有字段会混入本次修正。
    Object.keys(corrected).forEach((k) => delete corrected[k]);
    for (const field of data?.targetFields || []) corrected[field] = null;
    Object.assign(corrected, data?.finalValues || data?.suggestion || {});
    comment.value = "";
    if (data?.targetFields.includes("source_url") && !corrected.source_url) {
      const selectedUrls = Array.from(
        new Set(
          (data.evidence || [])
            .filter(
              (item) => item.sourceUrl && item.metadata?.selected === true,
            )
            .map((item) => item.sourceUrl!),
        ),
      );
      if (selectedUrls.length === 1) corrected.source_url = selectedUrls[0];
      // 多个采用来源存在歧义时不自动填写，交由审核员明确选择。
    }
  },
  { immediate: true },
);
async function decide(decision: string) {
  const u = context.data.value!;
  if (
    (decision === "CONFIRMED_UNRESOLVED" ||
      (decision === "CORRECTED" && u.executionStatus === "FAILED_FINAL")) &&
    !comment.value.trim()
  ) {
    ElMessage.warning("请填写人工调查或补录依据");
    return;
  }
  try {
    await post(`/api/v1/review-units/${u.id}`, {
      decision,
      expected_version: u.version,
      values: decision === "CORRECTED" ? corrected : undefined,
      comment: comment.value || undefined,
    });
    ElMessage.success("核对结果已保存");
    await qc.invalidateQueries({ queryKey: ["review-queue"] });
    const next = queue.data.value?.items.find((x: Unit) => x.id !== u.id);
    selected.value = next?.id || "";
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  }
}
async function bulkApprove() {
  if (!selectedIds.value.length) return;
  try {
    const preview = await post<{
      previewToken: string;
      eligible: number;
      excluded: number;
    }>(`/api/v1/tasks/${id}/reviews/bulk/preview`, {
      unit_ids: selectedIds.value,
      decision: "APPROVED",
    });
    await ElMessageBox.confirm(
      // 预检由服务端排除高风险或已变化记录，确认后用令牌原子提交同一集合。
      `可批准 ${preview.eligible} 条，排除 ${preview.excluded} 条。确认提交？`,
      "批量审核预览",
    );
    await post(`/api/v1/tasks/${id}/reviews/bulk/commit`, {
      preview_token: preview.previewToken,
    });
    selectedIds.value = [];
    await qc.invalidateQueries({ queryKey: ["review-queue"] });
    ElMessage.success("批量审核已提交");
  } catch (e) {
    if (e !== "cancel")
      ElMessage.error(e instanceof Error ? e.message : "批量审核失败");
  }
}
function shortcut(event: KeyboardEvent) {
  // 输入框内禁用快捷键，避免审核员录入文字时误提交决策。
  const target = event.target as HTMLElement;
  if (["INPUT", "TEXTAREA"].includes(target.tagName)) return;
  if (!context.data.value) return;
  const action = {
    a: "APPROVED",
    c: "CORRECTED",
    r: "REJECTED",
    u: "CONFIRMED_UNRESOLVED",
  }[event.key.toLowerCase()];
  if (action === "APPROVED" && isExecutionFailure.value) return;
  if (action) {
    event.preventDefault();
    void decide(action);
  }
}
onMounted(() => window.addEventListener("keydown", shortcut));
onBeforeUnmount(() => window.removeEventListener("keydown", shortcut));
</script>
<template>
  <!-- 左侧负责批量选择和导航，右侧只编辑当前单元并展示完整来源链路。 -->
  <div class="page-head">
    <div>
      <h1>逐行核对</h1>
      <div class="muted">
        左侧原始与过程数据，右侧采集建议、来源证据和最终值
      </div>
    </div>
    <el-select v-model="executionFilter" style="width: 170px"
      ><el-option
        v-for="x in executionFilterOptions"
        :key="x"
        :label="executionFilterLabel(x)"
        :value="x"
    /></el-select>
    <el-select v-model="filter" style="width: 180px"
      ><el-option
        v-for="x in reviewFilterOptions"
        :key="x"
        :label="getStatusPresentation(x).label"
        :value="x"
    /></el-select>
    <el-button
      type="primary"
      :disabled="!selectedIds.length"
      @click="bulkApprove"
    >
      预览并批量批准（{{ selectedIds.length }}）
    </el-button>
  </div>
  <div class="split">
    <el-card class="card"
      ><template #header
        >核对队列（{{ queue.data.value?.total || 0 }}）</template
      ><el-table
        :data="queue.data.value?.items"
        highlight-current-row
        @current-change="(r: Unit) => (selected = r?.id || '')"
        @selection-change="
          (rows: Unit[]) => (selectedIds = rows.map((row) => row.id))
        "
        height="650"
        ><el-table-column
          type="selection"
          width="44"
          :selectable="canBulkApprove"
        />
        <el-table-column label="行"
          ><template #default="s">{{
            s.row.record?.sourceRow || s.row.id.slice(0, 6)
          }}</template></el-table-column
        ><el-table-column label="执行"
          ><template #default="s"
            ><StatusTag
              :value="s.row.executionStatus" /></template></el-table-column
        ><el-table-column label="审核"
          ><template #default="s"
            ><StatusTag
              :value="s.row.reviewStatus" /></template></el-table-column
        ><el-table-column label="解决状态"
          ><template #default="s"
            ><StatusTag
              :value="s.row.resolutionStatus" /></template></el-table-column
        ><el-table-column label="字段"
          ><template #default="s">{{
            s.row.targetFields.map(fieldLabel).join("、")
          }}</template></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="page"
        :total="queue.data.value?.total || 0"
        :page-size="50"
        layout="prev,pager,next"
    /></el-card>
    <div v-if="context.data.value">
      <el-card class="card"
        ><template #header
          ><b
            >{{ context.data.value.record?.sheetName }} · 第
            {{ context.data.value.record?.sourceRow }} 行</b
          ></template
        ><el-tabs
          ><el-tab-pane label="原始数据"
            ><table class="json-table">
              <tr v-for="(v, k) in context.data.value.record?.rawData" :key="k">
                <td>{{ fieldLabel(String(k)) }}</td>
                <td>{{ v }}</td>
              </tr>
            </table></el-tab-pane
          ><el-tab-pane label="过程数据">
            <div v-if="isAiIndex" class="process-audit">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="证据核验">
                  <StatusTag
                    :value="
                      context.data.value.validation?.evidence_approved
                        ? 'MATCHED'
                        : 'UNRESOLVED'
                    "
                  />
                </el-descriptions-item>
                <el-descriptions-item label="行约束">
                  <StatusTag
                    :value="
                      context.data.value.validation?.contract_valid
                        ? 'MATCHED'
                        : 'UNMATCHED'
                    "
                  />
                </el-descriptions-item>
                <el-descriptions-item label="换算方式">
                  <StatusTag
                    :value="String(conversionAudit?.mode || 'NOT_EVALUATED')"
                  />
                </el-descriptions-item>
                <el-descriptions-item label="换算结果">
                  <StatusTag
                    :value="String(conversionAudit?.status || 'NOT_EVALUATED')"
                  />
                </el-descriptions-item>
              </el-descriptions>
              <div v-if="constraintAudit.length" class="constraint-list">
                <div
                  v-for="item in constraintAudit"
                  :key="item.field"
                  class="constraint-item"
                >
                  <span
                    >{{ fieldLabel(item.field) }}：{{ item.value ?? "—" }}</span
                  >
                  <StatusTag :value="item.matched ? 'MATCHED' : 'UNMATCHED'" />
                </div>
              </div>
            </div>
            <div v-else-if="isAlgorithmCollection" class="process-audit">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="榜单名次">
                  第 {{ algorithmAudit.rank ?? rowContract.rank ?? "—" }} 名
                </el-descriptions-item>
                <el-descriptions-item label="证据核验">
                  <StatusTag
                    :value="
                      context.data.value.validation?.evidence_approved
                        ? 'MATCHED'
                        : 'UNRESOLVED'
                    "
                  />
                </el-descriptions-item>
                <el-descriptions-item label="精确收藏数">
                  {{ algorithmAudit.exact_stargazers_count ?? "—" }}
                </el-descriptions-item>
                <el-descriptions-item label="收藏量换算">
                  {{
                    algorithmAudit.star_transform ||
                    "精确收藏数 ÷ 1000，向下取整"
                  }}
                </el-descriptions-item>
                <el-descriptions-item label="快照时间" :span="2">
                  {{ rowContract.snapshot_at || "—" }}
                </el-descriptions-item>
              </el-descriptions>
            </div>
            <div v-else-if="isForbesAi50" class="process-audit">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="页面位置（非排名）">
                  第
                  {{
                    forbesAudit.list_position ??
                    rowContract.list_position ??
                    "—"
                  }}
                  条
                </el-descriptions-item>
                <el-descriptions-item label="证据核验">
                  <StatusTag
                    :value="
                      context.data.value.validation?.evidence_approved
                        ? 'MATCHED'
                        : 'UNRESOLVED'
                    "
                  />
                </el-descriptions-item>
                <el-descriptions-item label="榜单年度">
                  {{ forbesAudit.rank_year ?? rowContract.rank_year ?? "—" }}
                </el-descriptions-item>
                <el-descriptions-item label="官方发布时间">
                  {{
                    forbesAudit.datasource_date ??
                    corrected.datasource_date ??
                    "—"
                  }}
                </el-descriptions-item>
                <el-descriptions-item label="官方融资原文">
                  {{ forbesAudit.funding_raw ?? "—" }}
                </el-descriptions-item>
                <el-descriptions-item label="程序换算公式">
                  {{ forbesAudit.funding_formula ?? "—" }}
                </el-descriptions-item>
                <el-descriptions-item label="本次快照时间" :span="2">
                  {{ rowContract.snapshot_at || "—" }}
                </el-descriptions-item>
              </el-descriptions>
              <div class="conversion-note">
                福布斯 AI 50
                按公司名称字母顺序展示且不设名次；这里的页面位置只用于切分单公司证据。
              </div>
            </div>
            <pre v-else>{{
              JSON.stringify(context.data.value.validation, null, 2)
            }}</pre></el-tab-pane
          ><el-tab-pane label="审核历史">
            <pre>{{ JSON.stringify(context.data.value.history, null, 2) }}</pre>
          </el-tab-pane></el-tabs
        ></el-card
      ><el-card class="card" style="margin-top: 16px"
        ><template #header><b>建议值与证据</b></template
        ><el-alert
          v-if="isExecutionFailure"
          title="该条目抓取最终失败，必须人工处置"
          :description="`失败原因：${context.data.value.error || '未记录'}。请完整填写目标字段并保存人工补录，或填写调查说明后确认无法解决；不能直接批准。`"
          type="error"
          :closable="false"
          show-icon
          style="margin-bottom: 16px"
        />
        >
        <div
          v-if="context.data.value.acquisitionAttempts?.length"
          class="acquisition-route"
        >
          <!-- 路径审计解释为何直取或降级搜索，便于定位采集设计问题。 -->
          <div class="route-title">
            <b>来源获取路径</b>
            <StatusTag
              :value="context.data.value.acquisitionAttempts[0].route"
            />
          </div>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="匹配结果">
              <StatusTag
                :value="
                  context.data.value.acquisitionAttempts[0].matchStatus ||
                  'NOT_EVALUATED'
                "
              />
            </el-descriptions-item>
            <el-descriptions-item label="匹配数量">
              {{ context.data.value.acquisitionAttempts[0].matchCount }} 条
            </el-descriptions-item>
            <el-descriptions-item label="来源缓存">
              {{
                context.data.value.acquisitionAttempts[0].persistentCacheHit
                  ? "跨任务缓存命中"
                  : context.data.value.acquisitionAttempts[0].cacheHit
                    ? "本次运行缓存命中"
                    : "本次获取"
              }}
            </el-descriptions-item>
            <el-descriptions-item
              v-if="context.data.value.acquisitionAttempts[0].reason"
              label="搜索降级原因"
              :span="3"
            >
              {{
                getReasonLabel(context.data.value.acquisitionAttempts[0].reason)
              }}
            </el-descriptions-item>
            <el-descriptions-item label="输入链接" :span="3">
              <a
                v-if="context.data.value.acquisitionAttempts[0].inputUrl"
                :href="context.data.value.acquisitionAttempts[0].inputUrl || ''"
                target="_blank"
                >{{ context.data.value.acquisitionAttempts[0].inputUrl }}</a
              >
              <span v-else>未提供</span>
            </el-descriptions-item>
            <el-descriptions-item
              v-if="
                context.data.value.acquisitionAttempts[0].normalizedUrl &&
                context.data.value.acquisitionAttempts[0].normalizedUrl !==
                  context.data.value.acquisitionAttempts[0].inputUrl
              "
              label="规范化链接"
              :span="3"
            >
              <a
                :href="
                  context.data.value.acquisitionAttempts[0].normalizedUrl || ''
                "
                target="_blank"
                >{{
                  context.data.value.acquisitionAttempts[0].normalizedUrl
                }}</a
              >
            </el-descriptions-item>
          </el-descriptions>
        </div>
        <div v-if="usesSearchFallback" class="search-fallback-audit">
          <div class="route-title">
            <b>搜索降级结果</b>
            <StatusTag value="SEARCH_FALLBACK" />
          </div>
          <div v-if="context.data.value.rowSearchAttempts?.length">
            <section
              v-for="search in context.data.value.rowSearchAttempts"
              :key="search.id"
              class="search-attempt"
            >
              <el-descriptions :column="3" border>
                <el-descriptions-item label="搜索词" :span="3">
                  {{ search.query }}
                </el-descriptions-item>
                <el-descriptions-item label="搜索服务">
                  {{ search.provider }}
                </el-descriptions-item>
                <el-descriptions-item label="搜索状态">
                  <StatusTag :value="search.status" />
                </el-descriptions-item>
                <el-descriptions-item label="返回结果">
                  {{ search.resultCount }} 条
                </el-descriptions-item>
              </el-descriptions>
              <el-alert
                v-if="search.resultCount === 0"
                title="本次搜索没有返回候选结果"
                description="已保留搜索词和执行记录，请人工改用更合适的关键词继续调查。"
                type="warning"
                :closable="false"
                show-icon
                class="search-empty"
              />
              <div v-else class="search-result-list">
                <article
                  v-for="result in search.results"
                  :key="`${search.id}-${result.rank}-${result.url}`"
                  class="search-result"
                >
                  <div class="search-result-head">
                    <span class="search-rank">{{ result.rank || "—" }}</span>
                    <a
                      :href="result.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      >{{ result.title || result.url }}</a
                    >
                  </div>
                  <p v-if="result.excerpt">{{ result.excerpt }}</p>
                  <div class="search-result-meta">
                    <span>{{ result.url }}</span>
                    <span v-if="result.engines.length">
                      搜索引擎：{{ result.engines.join("、") }}
                    </span>
                  </div>
                </article>
                <el-alert
                  v-if="search.results.length < search.resultCount"
                  :title="`搜索返回 ${search.resultCount} 条，其中 ${search.results.length} 条具有可安全打开的 HTTP(S) 链接`"
                  type="info"
                  :closable="false"
                  show-icon
                />
              </div>
            </section>
          </div>
          <el-alert
            v-else
            title="已记录搜索降级，但缺少逐项搜索审计"
            description="该记录来自旧版或异常中断流程，请结合采集尝试和原始条件人工调查。"
            type="warning"
            :closable="false"
            show-icon
          />
        </div>
        <el-descriptions :column="3" border style="margin-bottom: 16px"
          ><el-descriptions-item label="解决状态"
            ><StatusTag
              :value="
                context.data.value.resolutionStatus
              " /></el-descriptions-item
          ><el-descriptions-item label="原因">{{
            getReasonLabel(context.data.value.resolutionReason)
          }}</el-descriptions-item
          ><el-descriptions-item label="风险"
            ><StatusTag
              :value="context.data.value.riskLevel" /></el-descriptions-item
        ></el-descriptions>
        <div v-if="isAiIndex" class="conversion-card">
          <div class="route-title">
            <b>原始值与标准值换算</b>
            <StatusTag
              :value="String(conversionAudit?.mode || 'NOT_EVALUATED')"
            />
          </div>
          <div class="conversion-flow">
            <div>
              <span>来源原始数据</span>
              <strong
                >{{ corrected.be_data ?? "—" }}
                {{ corrected.be_unit || "（无量纲）" }}</strong
              >
            </div>
            <span class="conversion-arrow">→</span>
            <div>
              <span>标准数据</span>
              <strong
                >{{
                  conversionInputsChanged
                    ? "保存时自动重算"
                    : (conversionAudit?.result ?? corrected.data ?? "—")
                }}
                {{ rowContract.standard_unit || "（无量纲）" }}</strong
              >
            </div>
          </div>
          <el-descriptions v-if="conversionAudit" :column="2" border>
            <el-descriptions-item label="换算状态">
              <StatusTag
                :value="String(conversionAudit.status || 'NOT_EVALUATED')"
              />
            </el-descriptions-item>
            <el-descriptions-item label="规则版本">{{
              conversionAudit.rule_version || "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="计算公式" :span="2">{{
              conversionAudit.formula || "—"
            }}</el-descriptions-item>
            <el-descriptions-item
              v-if="conversionAudit.reason"
              label="说明"
              :span="2"
              >{{ conversionAudit.reason }}</el-descriptions-item
            >
          </el-descriptions>
          <div class="conversion-note">
            标准值由系统根据来源原始值、来源原始单位和标准单位生成，人工修正时会再次重算。
          </div>
        </div>
        <div v-if="isAlgorithmCollection" class="conversion-card">
          <div class="route-title">
            <b>GitHub 月度前十快照</b>
            <StatusTag value="DIRECT_LINK" />
          </div>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="当前排名"
              >第 {{ corrected.rank ?? "—" }} 名</el-descriptions-item
            >
            <el-descriptions-item label="项目名称">{{
              corrected.name ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="收藏量"
              >{{ corrected.star ?? "—" }}
              {{ corrected.star_unit || "k" }}</el-descriptions-item
            >
            <el-descriptions-item label="采集时间" :span="3">{{
              corrected.collect_date ?? "—"
            }}</el-descriptions-item>
          </el-descriptions>
          <div class="conversion-note">
            排名、时间、来源和固定元数据由系统生成；人工只需在必要时修正项目名称或整数
            k 收藏量。
          </div>
        </div>
        <div v-if="isForbesAi50" class="conversion-card">
          <div class="route-title">
            <b>福布斯年度 AI 50 官方快照</b>
            <StatusTag value="DIRECT_LINK" />
          </div>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="公司">{{
              corrected.company_name ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="总部">{{
              corrected.headquarter_location ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="首席执行官">{{
              corrected.CEO ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="筹资金额"
              >{{ corrected.financing_amount ?? "—" }}
              {{
                corrected.financing_amount_unit || "亿美元"
              }}</el-descriptions-item
            >
            <el-descriptions-item label="成立时间">{{
              corrected.establish_date ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="榜单年度">{{
              corrected.rank_year ?? "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="采集时间" :span="3">{{
              corrected.collection_date ?? "—"
            }}</el-descriptions-item>
          </el-descriptions>
          <div class="conversion-note">
            融资额优先由官方百万美元数值确定性换算为亿美元；来源、年度、时间和批次状态由系统锁定。正式导出完整
            50 家后，旧活动批次才会统一标记为删除。
          </div>
        </div>
        <el-form label-position="top"
          ><el-form-item
            v-for="field in context.data.value.targetFields"
            :key="field"
            :label="fieldLabel(field)"
            ><el-input
              v-if="
                (isAlgorithmCollection &&
                  algorithmApplicationFields.has(field)) ||
                (isForbesAi50 &&
                  forbesApplicationFields.has(field) &&
                  !(isExecutionFailure && field === 'datasource_date'))
              "
              :model-value="corrected[field]"
              disabled
            />
            <el-select
              v-else-if="field === 'source_url'"
              v-model="corrected[field]"
              filterable
              allow-create
              default-first-option
              placeholder="选择已采集的证据链接，或粘贴新的对应来源"
              style="width: 100%"
            >
              <!-- 可复用证据链接，也允许人工粘贴经核验的新来源。 -->
              <el-option
                v-for="item in evidenceSourceOptions"
                :key="item.sourceUrl"
                :label="item.title || item.sourceUrl"
                :value="item.sourceUrl"
              />
            </el-select>
            <el-input
              v-else-if="isAiIndex && field === 'data'"
              :model-value="
                conversionInputsChanged ? '保存时自动重算' : corrected[field]
              "
              disabled
              placeholder="由系统自动换算，无需人工填写"
            />
            <el-input
              v-else
              v-model="corrected[field]"
            /> </el-form-item></el-form
        ><el-collapse
          ><el-collapse-item
            :title="`来源证据（${context.data.value.evidence?.length || 0}）`"
            ><div
              v-for="e in context.data.value.evidence"
              :key="e.id"
              style="padding: 10px 0; border-bottom: 1px solid #eee"
            >
              <a v-if="e.sourceUrl" :href="e.sourceUrl" target="_blank">{{
                e.title || e.sourceUrl
              }}</a>
              <p>{{ e.excerpt }}</p>
            </div></el-collapse-item
          ><el-collapse-item
            :title="`采集尝试（${context.data.value.collectionAttempts?.length || 0}）`"
          >
            <el-timeline>
              <el-timeline-item
                v-for="attempt in context.data.value.collectionAttempts"
                :key="attempt.id"
                :timestamp="attempt.startedAt"
                placement="top"
              >
                <b>{{ attempt.step }}</b>
                <StatusTag :value="attempt.status" />
                <p v-if="attempt.error" class="attempt-error">
                  {{ attempt.error }}
                </p>
                <pre v-if="Object.keys(attempt.outputSummary || {}).length">{{
                  JSON.stringify(attempt.outputSummary, null, 2)
                }}</pre>
              </el-timeline-item>
            </el-timeline>
          </el-collapse-item></el-collapse
        >
        <div class="actions" style="margin-top: 18px">
          <el-input
            v-model="comment"
            type="textarea"
            :rows="2"
            :placeholder="
              isExecutionFailure
                ? '必填：说明人工补录来源，或记录确认无法解决的调查过程'
                : '修正、驳回或确认未解决时填写调查说明'
            "
            style="margin-bottom: 12px"
          />
          <el-button
            v-if="!isExecutionFailure"
            type="success"
            @click="decide('APPROVED')"
            >确认建议（A）</el-button
          ><el-button type="primary" @click="decide('CORRECTED')"
            >保存修正（C）</el-button
          ><el-button
            v-if="
              isExecutionFailure ||
              ['PARTIAL', 'UNRESOLVED', 'CONFLICT'].includes(
                context.data.value.resolutionStatus,
              )
            "
            type="warning"
            @click="decide('CONFIRMED_UNRESOLVED')"
            >确认未解决（U）</el-button
          ><el-button type="danger" plain @click="decide('REJECTED')"
            >驳回重采（R）</el-button
          >
        </div></el-card
      >
    </div>
  </div>
</template>

<style scoped>
.acquisition-route {
  padding: 14px;
  margin-bottom: 16px;
  border: 1px solid #dbe7f5;
  border-radius: 10px;
  background: #f8fbff;
}
.search-fallback-audit {
  padding: 14px;
  margin-bottom: 16px;
  border: 1px solid #f4d19b;
  border-radius: 10px;
  background: #fffaf2;
}
.search-attempt + .search-attempt {
  margin-top: 18px;
}
.search-empty,
.search-result-list {
  margin-top: 12px;
}
.search-result-list {
  display: grid;
  gap: 10px;
}
.search-result {
  padding: 12px;
  border: 1px solid #eadfcf;
  border-radius: 8px;
  background: #fff;
}
.search-result-head {
  display: flex;
  gap: 9px;
  align-items: flex-start;
}
.search-rank {
  display: inline-grid;
  flex: 0 0 24px;
  height: 24px;
  place-items: center;
  border-radius: 50%;
  color: #92400e;
  background: #fef3c7;
  font-size: 12px;
  font-weight: 700;
}
.search-result p {
  margin: 8px 0;
  color: #475569;
  line-height: 1.6;
}
.search-result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  color: #64748b;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.route-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.process-audit,
.conversion-card {
  display: grid;
  gap: 14px;
}
.conversion-card {
  padding: 14px;
  margin-bottom: 16px;
  border: 1px solid #c7e8d1;
  border-radius: 10px;
  background: #f6fdf8;
}
.conversion-flow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}
.conversion-flow > div {
  display: grid;
  gap: 4px;
  padding: 12px;
  border-radius: 8px;
  background: #fff;
}
.conversion-flow span,
.conversion-note {
  color: #64748b;
  font-size: 13px;
}
.conversion-flow strong {
  font-size: 17px;
}
.conversion-arrow {
  color: #16a34a !important;
  font-size: 22px !important;
}
.constraint-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 8px;
}
.constraint-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 11px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.attempt-error {
  color: #b91c1c;
  white-space: pre-wrap;
}
</style>
