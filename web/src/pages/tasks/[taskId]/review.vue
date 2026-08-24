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
const page = ref(1);
const selected = ref("");
const queue = useQuery({
  queryKey: ["review-queue", id, filter, page],
  queryFn: () =>
    api<{ items: Unit[]; total: number }>(
      `/api/v1/tasks/${id}/review-queue?reviewStatus=${filter.value}&offset=${(page.value - 1) * 50}&limit=50`,
    ),
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
watch(
  () => context.data.value,
  (data) => {
    // 切换行时必须先清空 reactive 对象，否则上一行特有字段会混入本次修正。
    Object.keys(corrected).forEach((k) => delete corrected[k]);
    Object.assign(corrected, data?.suggestion || {});
    if (
      data?.targetFields.includes("source_url") &&
      !corrected.source_url
    ) {
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
        左侧原始与过程数据，右侧模型建议、来源证据和最终值
      </div>
    </div>
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
        ><el-table-column type="selection" width="44" />
        <el-table-column label="行"
          ><template #default="s">{{
            s.row.record?.sourceRow || s.row.id.slice(0, 6)
          }}</template></el-table-column
        ><el-table-column label="状态"
          ><template #default="s"
            ><StatusTag
              :value="s.row.reviewStatus" /></template></el-table-column
        ><el-table-column label="解决状态"
          ><template #default="s"
            ><StatusTag
              :value="s.row.resolutionStatus" /></template></el-table-column
        ><el-table-column label="字段"
          ><template #default="s">{{
            s.row.targetFields.join("、")
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
                <td>{{ k }}</td>
                <td>{{ v }}</td>
              </tr>
            </table></el-tab-pane
          ><el-tab-pane label="过程数据">
            <pre>{{
              JSON.stringify(context.data.value.validation, null, 2)
            }}</pre></el-tab-pane
          ><el-tab-pane label="审核历史">
            <pre>{{ JSON.stringify(context.data.value.history, null, 2) }}</pre>
          </el-tab-pane></el-tabs
        ></el-card
        ><el-card class="card" style="margin-top: 16px"
        ><template #header><b>建议值与证据</b></template
        ><div
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
                getReasonLabel(
                  context.data.value.acquisitionAttempts[0].reason,
                )
              }}
            </el-descriptions-item>
            <el-descriptions-item label="输入链接" :span="3">
              <a
                v-if="context.data.value.acquisitionAttempts[0].inputUrl"
                :href="context.data.value.acquisitionAttempts[0].inputUrl || ''"
                target="_blank"
              >{{ context.data.value.acquisitionAttempts[0].inputUrl }}</a>
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
              >{{ context.data.value.acquisitionAttempts[0].normalizedUrl }}</a>
            </el-descriptions-item>
          </el-descriptions>
        </div
        ><el-descriptions :column="3" border style="margin-bottom: 16px"
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
              :value="
                context.data.value.riskLevel
              " /></el-descriptions-item></el-descriptions
        ><el-form label-position="top"
          ><el-form-item
            v-for="field in context.data.value.targetFields"
            :key="field"
            :label="field"
            ><el-select
              v-if="field === 'source_url'"
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
            <el-input v-else v-model="corrected[field]" /></el-form-item></el-form
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
          ></el-collapse
        >
        <div class="actions" style="margin-top: 18px">
          <el-input
            v-model="comment"
            type="textarea"
            :rows="2"
            placeholder="修正、驳回或确认未解决时填写调查说明"
            style="margin-bottom: 12px"
          />
          <el-button type="success" @click="decide('APPROVED')"
            >确认建议（A）</el-button
          ><el-button type="primary" @click="decide('CORRECTED')"
            >保存修正（C）</el-button
          ><el-button
            v-if="
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
.route-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
</style>
