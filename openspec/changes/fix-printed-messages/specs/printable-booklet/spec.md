# Spec Delta

## Purpose

このcapabilityは、A4横向きの冊子の紙面組成を扱う。本deltaは、TODO面の幾何が旧Android版の組成に一致することと、支線（装饰のための余分な線）を禁止することを規格に加える。

## ADDED Requirements

### Requirement: TODO面の組成

TODO面は、見出し「TODO」、各項目の絞め丸（checkbox相当の輪郭）と項目行の配置を、旧Android版の対応する紙面と同じ姿をしなければならない（SHALL）。項目間や見出しに、チェックの連結や折り目以外の目的で斜めの支線を描いてはならない（MUST NOT）。

#### Scenario: TODO面を印刷して見る

- **WHEN** 期限つきTODOを冊子へ載せる
- **THEN** 見出しと丸と項目行は旧Android版と同じ位置関係で並び、円から項目欄の外へ延びる斜線はどこにも現れない
