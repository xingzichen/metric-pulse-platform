<script setup lang="ts">
const props = defineProps<{ value: string }>();
const success = ["READY", "SUCCEEDED", "APPROVED", "CORRECTED", "PUBLISHED"];
const warning = [
  "PAUSED",
  "PAUSING",
  "QUEUED",
  "UNREVIEWED",
  "NEEDS_CONFIRMATION",
  "SUCCEEDED_WITH_ERRORS",
];
const danger = ["FAILED", "FAILED_FINAL", "REJECTED", "STOPPED", "STALE"];
const type = success.includes(props.value)
  ? "success"
  : warning.includes(props.value)
    ? "warning"
    : danger.includes(props.value)
      ? "danger"
      : "info";
</script>
<template>
  <el-tag :type="type" effect="light">{{ value }}</el-tag>
</template>
