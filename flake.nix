# agentic-jobseek toolkit — the agentic-nomad flake shape, trimmed to what this repo runs.
#
#   nix develop      → jq, psql, poppler (pdfinfo/pdftotext), python3, shellcheck, gh, git
#   nix fmt          → treefmt (nixfmt + ruff-format + shfmt)
#   nix flake check  → formatting + toolchain-complete + project-gate (OFFLINE: shellcheck over
#                      scripts/, and a new-application.sh scaffold into a fake data folder)
#
# NOT in the shell on purpose: `jsonresume`. It is the fleet's wrapper (kattakath/nix-config
# packages/jsonresume.nix) and renders through a system Chrome; vendoring it here would be a
# second copy to drift. It stays an operator-PATH tool, like the local pgvector + Ollama store.
{
  description = "agentic-jobseek — an evidence-gated job search run from Claude Code; data lives in Drive";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      treefmt-nix,
    }:
    let
      # x86_64-darwin is absent on purpose: nixpkgs-unstable throws on it.
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f system nixpkgs.legacyPackages.${system});
      inherit (nixpkgs) lib;

      treefmtEval = forAll (_: pkgs: treefmt-nix.lib.evalModule pkgs ./treefmt.nix);

      # ONE list read by devShells AND toolchain-complete, so they cannot drift.
      devPackagesFor =
        pkgs: with pkgs; [
          jq
          postgresql
          poppler-utils
          (python3.withPackages (ps: [ ps.jsonschema ]))
          shellcheck
          gh
          git
        ];
      # Binaries the scripts and skills shell out to. A package name is not a binary name
      # (postgresql ships psql; poppler-utils ships pdfinfo) — that map is what this guards.
      requiredBins = [
        "jq"
        "psql"
        "pdfinfo"
        "pdftotext"
        "python3"
        "shellcheck"
        "gh"
        "git"
      ];
    in
    {
      formatter = forAll (system: _: treefmtEval.${system}.config.build.wrapper);

      devShells = forAll (
        _system: pkgs: {
          default = pkgs.mkShell {
            packages = devPackagesFor pkgs;
            shellHook = ''
              [ -n "''${JOBSEEK_DRIVE_DIR:-}" ] || echo "agentic-jobseek: JOBSEEK_DRIVE_DIR is unset — see .envrc"
            '';
          };
        }
      );

      checks = forAll (
        system: pkgs:
        let
          gateSrc = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./scripts
              ./templates
              ./schemas
            ];
          };
        in
        {
          formatting = treefmtEval.${system}.config.build.check self;

          toolchain-complete =
            pkgs.runCommandLocal "toolchain-complete" { nativeBuildInputs = devPackagesFor pkgs; }
              ''
                missing=""
                for bin in ${lib.escapeShellArgs requiredBins}; do
                  command -v "$bin" >/dev/null 2>&1 || missing="$missing $bin"
                done
                if [ -n "$missing" ]; then
                  echo "dev shell is missing:$missing" >&2
                  exit 1
                fi
                echo "all ${toString (builtins.length requiredBins)} required binaries resolve." > "$out"
              '';

          # Offline: no Drive, no database. Each command on its own line — under the builder's
          # `set -e` a failing `a` in an `a && b` list does not stop the script.
          project-gate =
            pkgs.runCommandLocal "project-gate"
              {
                nativeBuildInputs = with pkgs; [
                  shellcheck
                  (python3.withPackages (ps: [ ps.jsonschema ]))
                  curl
                ];
              }
              ''
                cp -R ${gateSrc}/. gate
                chmod -R u+w gate
                cd gate
                shellcheck scripts/*.sh
                python3 -m py_compile scripts/*.py

                mkdir -p fake
                echo '{"basics":{"name":"Jane Doe"}}' > fake/master.json
                export JOBSEEK_MASTER_URL="file://$PWD/fake/master.json"
                JOBSEEK_DRIVE_DIR="$PWD/fake" bash scripts/new-application.sh "Acme Corp" "Staff Engineer"
                app=fake/applications/acme-corp/staff-engineer
                test -f "$app/posting.md"
                test -f "$app/resume.json"
                if grep -rl '{{' "$app"; then echo "placeholders left in the scaffold" >&2; exit 1; fi
                if JOBSEEK_DRIVE_DIR="$PWD/fake" bash scripts/new-application.sh "Acme Corp" "Staff Engineer"; then
                  echo "a duplicate scaffold was not refused" >&2; exit 1
                fi
                # job.json: the scaffolded template must FAIL validation (empty arrays), a filled one must PASS
                if python3 scripts/job-validate.py "$app/job.json"; then
                  echo "an unfilled job.json passed validation" >&2; exit 1
                fi
                python3 - "$app/job.json" <<'PY'
                import json, sys
                p = sys.argv[1]; j = json.load(open(p))
                j.update(responsibilities=["Build APIs"], qualifications=["5+ years"], skills=[{"name": "AWS", "keywords": ["Lambda"]}])
                json.dump(j, open(p, "w"))
                PY
                test "$(python3 scripts/job-validate.py "$app/job.json" --items | wc -l)" -eq 3
                if env -u JOBSEEK_DRIVE_DIR bash scripts/new-application.sh a b; then
                  echo "an unset JOBSEEK_DRIVE_DIR was not refused" >&2; exit 1
                fi
                touch "$out"
              '';
        }
      );
    };
}
