// semantic-release 配置（原放在 package.json 的 release 字段，2026-10-03 迁出以按分支区分插件）。
//
// main：正式发版，写 CHANGELOG + 各文件版本号并打 tag（release.yml 随后打包 Win/macOS）。
// develop：预发布通道，只发布 x.y.z-dev.N 的 GitHub Prerelease，不写任何文件——
//   develop 的版本标记由 sync-main-into-develop.yml 用「已发布版本 + -dev」统一管理，
//   两边都改 CHANGELOG/版本号会让每周的 develop→main 合并必然冲突，且版本号来回跳。
const branchName = (process.env.GITHUB_REF_NAME || '').trim();
const isPrerelease = branchName === 'develop';

const plugins = [
  '@semantic-release/commit-analyzer',
  [
    '@semantic-release/release-notes-generator',
    {
      writerOpts: {
        footerPartial:
          '{{#if noteGroups}}\n{{#each noteGroups}}\n\n### {{title}}\n\n{{#each notes}}\n* {{text}}\n{{/each}}\n{{/each}}\n{{/if}}\n---\n## 下载说明\n\nmacOS 用户请按芯片架构选择安装包：\n\n- **Apple Silicon (M1/M2/M3/M4)**：下载 `{{version}}-arm64.dmg`（ARM 架构）\n- **Intel**：下载 `{{version}}.dmg`（x86 无后缀）\n',
      },
    },
  ],
];

if (!isPrerelease) {
  plugins.push(
    [
      '@semantic-release/changelog',
      {
        changelogFile: 'CHANGELOG.md',
      },
    ],
    [
      '@semantic-release/exec',
      {
        prepareCmd: 'bash scripts/update-version.sh ${nextRelease.version}',
      },
    ],
    [
      '@semantic-release/git',
      {
        assets: ['CHANGELOG.md', 'package.json', 'pyproject.toml', 'apps/desktop/package.json', 'miqi/__init__.py'],
        message: 'chore(release): ${nextRelease.version} [skip ci]',
      },
    ],
  );
}

plugins.push('@semantic-release/github');

module.exports = {
  branches: ['main', { name: 'develop', prerelease: 'dev' }],
  plugins,
};
