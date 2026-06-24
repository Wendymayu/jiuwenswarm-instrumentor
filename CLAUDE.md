# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

**jiuwenswarm-instrumentor** is an observability data collector ("可观测数据采集器") for
jiuwenswarm agents (智能体). The repository name (`-instrumentor`) indicates it will provide
instrumentation that captures observability data from agents in the jiuwenswarm system.

> Source: `README.md` — the only authoritative description available.

## Current State

This repository is in its initial state. As of this writing it contains **no source code,
build system, dependency manifests, tests, or configuration files** — only `README.md`.

Because there is no code yet:

- There are **no build, lint, or test commands** to document. Do not invent them.
  Once a language/toolchain is chosen (e.g. Go, Python, Rust, Node), add the
  corresponding commands here.
- There is **no architecture** to describe yet. Add an architecture overview once
  the first modules exist.

When this changes, update this file with: the chosen toolchain, build/test/lint
commands, how to run a single test, and a high-level architecture summary that
spans more than one file.
