<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { ElMessage } from "element-plus";
import { api, post } from "../../../api";
import type { Unit } from "../../../types";
import StatusTag from "../../../components/StatusTag.vue";
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
watch(
  () => context.data.value?.suggestion,
  (x) => {
    Object.keys(corrected).forEach((k) => delete corrected[k]);
    Object.assign(corrected, x || {});
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
    });
    ElMessage.success("核对结果已保存");
    await qc.invalidateQueries({ queryKey: ["review-queue"] });
    const next = queue.data.value?.items.find((x: Unit) => x.id !== u.id);
    selected.value = next?.id || "";
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  }
}
</script>
<template>
  <div class="page-head">
    <div>
      <h1>逐行核对</h1>
      <div class="muted">
        左侧原始与过程数据，右侧模型建议、来源证据和最终值
      </div>
    </div>
    <el-select v-model="filter" style="width: 180px"
      ><el-option
        v-for="x in [
          'UNREVIEWED',
          'APPROVED',
          'CORRECTED',
          'REJECTED',
          'SKIPPED',
        ]"
        :key="x"
        :value="x"
    /></el-select>
  </div>
  <div class="split">
    <el-card class="card"
      ><template #header
        >核对队列（{{ queue.data.value?.total || 0 }}）</template
      ><el-table
        :data="queue.data.value?.items"
        highlight-current-row
        @current-change="(r: Unit) => (selected = r?.id || '')"
        height="650"
        ><el-table-column label="行"
          ><template #default="s">{{
            s.row.record?.sourceRow || s.row.id.slice(0, 6)
          }}</template></el-table-column
        ><el-table-column label="状态"
          ><template #default="s"
            ><StatusTag
              :value="s.row.reviewStatus" /></template></el-table-column
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
        ><el-form label-position="top"
          ><el-form-item
            v-for="field in context.data.value.targetFields"
            :key="field"
            :label="field"
            ><el-input v-model="corrected[field]" /></el-form-item></el-form
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
          <el-button type="success" @click="decide('APPROVED')"
            >确认建议</el-button
          ><el-button type="primary" @click="decide('CORRECTED')"
            >保存修正</el-button
          ><el-button type="danger" plain @click="decide('REJECTED')"
            >驳回重采</el-button
          ><el-button @click="decide('SKIPPED')">暂不处理</el-button>
        </div></el-card
      >
    </div>
  </div>
</template>
