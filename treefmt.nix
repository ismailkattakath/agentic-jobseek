# Read by both `formatter` (nix fmt) and the `formatting` check, so they never disagree.
{
  projectRootFile = "flake.nix";
  programs.nixfmt.enable = true;
  programs.ruff-format.enable = true;
  programs.shfmt.enable = true;
  settings.global.excludes = [
    "*.lock"
    "flake.lock"
    "result"
    "result-*"
  ];
}
