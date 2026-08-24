<template>
  <!-- 文件路由未匹配时提供可恢复入口，不暴露内部路由或服务器错误。 -->
  <el-result icon="warning" title="页面不存在" sub-title="请从左侧导航返回"
    ><template #extra
      ><el-button type="primary" @click="$router.push('/')"
        >回到工作台</el-button
      ></template
    ></el-result
  >
</template>
