import {homedir} from 'node:os';
import path from 'node:path';

// Operator-owned recording. Pins are data identities, not browser-supplied paths.
// No captures, harness content or generated source are bundled with the app.
export const recordedHarnessConfig = {
  root: path.join(homedir(), 'nomos_data/harness2-runs/mojo-glimmer-20261031'),
  task: 'generics-largest-element',
  base: {id:'c1c27817abff', directory:'frozen/base', sha256:'c1c27817abff98861987d8b97a548c11f1e1db0a3fb0aef5d607c8c23ff8050e'},
  improved: {id:'f268b624138c', directory:'frozen/primary-simd-fixed-dot-product-agg1', sha256:'f268b624138c66d6cfebd57d1c7b998cf588a3ba152a88a1abbcc49c578fc7b0'},
  pins: {
  "RESULTS.md": "00b11c95f9356f49d1ca42a5b3d581f3ba5cd562e7a57049c4409ee5c6a03d46",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/stderr.txt": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/stderr.txt": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "PREREGISTRATION-RUN2.md": "e703978270f27d01da05f5bb420c30c666ba5f3d0c6edfde319294724b92cabf",
  "frozen/base/LongTermMEMORY.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "frozen/base/guardrails.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "frozen/base/skills/mojo-core/SKILL.md": "82ea4b9edc642a6436302109ed96c1f890430c0b52774cd934ee807756f8bf5d",
  "frozen/base/systemprompt.md": "373caa008cda9aa4a9d016f92a72687c9f357abb410763c5c5d751c52301af9b",
  "frozen/base/tool_descriptions/tool_guidance.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "frozen/primary-simd-fixed-dot-product-agg1/LongTermMEMORY.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "frozen/primary-simd-fixed-dot-product-agg1/extensions/README.md": "c1348b89167e42af9b0eb693fa66f3b1885b41098ccec5c4ae185641b0ba74e2",
  "frozen/primary-simd-fixed-dot-product-agg1/extensions/mojo-verify-note.ts": "d89831ff500f86273ebac521fe3687ad0a3deadd1cdef8b083cb2506712f4b8e",
  "frozen/primary-simd-fixed-dot-product-agg1/guardrails.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "frozen/primary-simd-fixed-dot-product-agg1/scripts/README.md": "a511a671ae2d8097770dce7bbb7c8db16e17bd8dd0b19b199ed095a7f7e8b3f5",
  "frozen/primary-simd-fixed-dot-product-agg1/skills/README.md": "025492582970ec42d046e16302b9ccb7d265cd47511ccd8e03a6885be5426da1",
  "frozen/primary-simd-fixed-dot-product-agg1/skills/mojo-core/SKILL.md": "136310a377ace617aa805f9a063a1b9a0f862d9669a08038643306beb96ba8a0",
  "frozen/primary-simd-fixed-dot-product-agg1/skills/mojo-simd/SKILL.md": "70473337bf20c895481bab5b6640eb6542e5c7730b917ba60cec0dde252a30ce",
  "frozen/primary-simd-fixed-dot-product-agg1/skills/mojo-verify/SKILL.md": "71f6baf4cd8309ec69b6b52c430ffca014f2d76eb90e6ac8b5a166bbdffcc637",
  "frozen/primary-simd-fixed-dot-product-agg1/skills/mojo-verify/scripts/check_mojo.py": "31e923f5d6ae55e64a0ecb44e4538bf7b1b18cddcd502178eef2a635b2a6563c",
  "frozen/primary-simd-fixed-dot-product-agg1/sub_agents/README.md": "bfbd8ded1dc7889b77356041728976130df783a9b90b27c4fda0e3878844d352",
  "frozen/primary-simd-fixed-dot-product-agg1/systemprompt.md": "9174fd1625774c18a75e490438178c25d596f3bd00fa92a21ebc6b4a78d048ce",
  "frozen/primary-simd-fixed-dot-product-agg1/tool_descriptions/tool_guidance.md": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "mojo/evaluate-test-c1c27817abff-driver.json": "1af53485e06fa6510a4caa26fd04aa233d31ce6e5df741c77cb374a53e63060b",
  "mojo/evaluate-test-f268b624138c-driver.json": "661bb8f1ba80be943ab8a8df12f85bfc3e5dce3e53919d8234ff04ebbd556440",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/complete.json": "bb5cbac1674a6dcb8e236b565f4f5da7a6bca649fdb8dbce6caea43966582aee",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/launch.json": "51c427c9248c6d44ccf368ee0520594d0c8508936e50d473f8c15a57acb56c37",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/mojo-grade.json": "39e569dfa94801d0bb184840145d8899333ce09a104a34115127ae38df2bffaf",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/pi.jsonl": "1ca129f22f2b4d807ac3928ffb3eb2db5e1ccfe4bf93d0db22c369364bfca756",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/result.json": "8979192cd509792970b738a4f8efe6a87a096822ce2416e64154aaa639cda07d",
  "mojo/executions/evaluate-test-c1c27817abff/generics-largest-element/solution.mojo": "0fe56750119db65e05c9e9adae764780fce46f40b5113dc10a8c108f164fe510",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/complete.json": "50cb954308c9319857c8030ac4ce12d0d17c5f71301d902f164754b9e47b3a3a",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/launch.json": "4497946b4b473890f116a1f17631fc29a37b0c76ce2c6b2bc88b8631e5f8d39f",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/mojo-grade.json": "08e1b0dc57580c181393071540180185d9a9944e4aea27b37ee39325a7853a46",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/pi.jsonl": "95bf072e12808673d4b8227e62dea72d7f4bcb4db6b2d369618f81130a2b0b58",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/result.json": "c72c6830bb41488dbb9ca88a237e7753257bdd0955ac00bca0873c1ff0ad9ba0",
  "mojo/executions/evaluate-test-f268b624138c/generics-largest-element/solution.mojo": "a58f1c660b62cb402a928fa38220ac8bf02895f2011786aeaa1735e8701bacfd",
  "split-run2.json": "eb5ac1faa02cb3c3f9e631ae69e02866ac18a5a7ad80df9e487239126cdaa941"
}
};
