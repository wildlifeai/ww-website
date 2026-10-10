// Conventional Commits, the same rules as ww-mobile-app. The frontend/.husky/commit-msg
// hook checks each commit locally and .github/workflows/commitlint.yml checks every commit
// in a pull request. Every line, header, body and footer, is at most 100 characters.
export default {
  extends: ['@commitlint/config-conventional'],
}
