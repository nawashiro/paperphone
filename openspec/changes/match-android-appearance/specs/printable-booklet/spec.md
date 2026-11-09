# Spec Delta

## ADDED Requirements

### Requirement: 表紙の見出し
アプリは表紙の見出しを翻訳カタログの適用外にしなければならない（MUST）。見出しは既存の紙面と同じ組み立てに従い、氏名を大文字にした「(氏名)'S」の段と「PHONE」の段で構成し、下端に日付を置かなければならない（SHALL）。見出しの文言は、氏名以外を翻訳で置き換えてはならない（MUST NOT）。

#### Scenario: 日本語環境で表紙を生成する
- **WHEN** UI言語が日本語の状態で氏名`Nawashiro`の氏名で冊子を生成する
- **THEN** 表紙見出しは「NAWASHIRO'S」と「PHONE」の2段になり、「NAWASHIROの」や「Paper Phone」の文言は出ない

#### Scenario: 氏名の大文字化
- **WHEN** 氏名に小文字が含まれる
- **THEN** 見出しは氏名を大文字にした形で表示し、氏名以外の文言はどの言語設定でも同じである
