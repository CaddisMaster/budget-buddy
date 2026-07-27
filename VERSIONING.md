# Versioning

Budget Buddy uses the **shape** of [Semantic Versioning](https://semver.org)
(`MAJOR.MINOR.PATCH`) while it sits in `0.x`, which means the usual MAJOR
guarantees do not apply yet.

**This is not a compatibility contract.** Budget Buddy is a single-deployment,
self-hosted application with no downstream consumers — nobody imports it as a
library, and no third party pins a version of it. Version numbers here are
discipline and a changelog anchor, not a promise to anyone.

## The scheme

| Bump | When | Example |
|---|---|---|
| `0.MINOR.0` | Any release carrying a feature — even several at once | `0.1.0` → `0.2.0` |
| `0.MINOR.PATCH` | Fixes only, no new surface | `0.2.0` → `0.2.1` |
| `1.0.0` | Cut only when the project is deliberately declared stable | — |

While the leading digit is `0`, a MINOR bump may include a breaking change.
When that happens it is called out at the top of the release's changelog entry
under a `### Breaking` heading.

## The release is the unit, not the feature

A release bundles **everything merged to `main` since the previous release**
into one version bump. Three features and a fix that land across two weeks ship
as a single MINOR — they do not get a version each. Every bundled item is listed
in that release's `CHANGELOG.md` entry.

Releases happen when the maintainer decides there is something worth shipping.
There is no fixed cadence, and no work is scheduled around a ship day. (An
earlier fixed-day experiment was abandoned precisely because finished features
sat unused waiting for it.)

## Version numbers only ever climb

Versions increase monotonically from the current tag. Already-published tags are
never rewritten, moved, or reused.

## Why the numbering restarted at 0.1.0

This repository begins at `0.1.0`. The application it contains is mature and had
previously been released through `v10.15.0` in an earlier repository, which is
archived read-only at
[CaddisMaster/budget-buddy-archive](https://github.com/CaddisMaster/budget-buddy-archive).

That old scheme had a MAJOR digit frozen at `10` with a `v11.0.0` reserved for a
break that was never going to come — a versioning contract for consumers who do
not exist. Rather than carry a misleading number forward, the reset states the
honest position: a `0.x` project, stabilising in the open, with its full prior
history one click away.

The application was **not** rewritten. See `CHANGELOG.md` for what `0.1.0`
actually contains.
