<template>
  <el-result icon="warning" title="页面不存在" sub-title="请从左侧导航返回"
    ><template #extra
      ><el-button type="primary" @click="$router.push('/')"
        >回到工作台</el-button
      ></template
    ></el-result
  >
</template>
