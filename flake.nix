{
  description = "driftwm — a trackpad-first infinite canvas Wayland compositor";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      nativeBuildInputs = with pkgs; [
        makeWrapper
        pkg-config
      ];

      buildInputs = with pkgs; [
        wayland
        wayland-protocols
        seatd # libseat
        libdisplay-info
        libinput
        libgbm
        libxkbcommon
        libdrm
        systemd # libudev
        libglvnd
        libx11
        libxcursor
        libxrandr
        libxi
        libxcb
        pixman
      ];

      runtimeLibs = with pkgs; [
        wayland
        seatd
        libdisplay-info
        libinput
        libgbm
        libxkbcommon
        libdrm
        systemd
        libglvnd
        libx11
        libxcursor
        libxrandr
        libxi
        libxcb
        pixman
      ];

      nsoPython = pkgs.python313.withPackages (ps: [ ps.pygobject3 ps.pycairo ]);
      nsoRuntimePath = pkgs.lib.makeBinPath [
        pkgs.glib.out
        pkgs.playerctl
        pkgs.xdg-utils
      ];
      nsoTypelibPath = pkgs.lib.makeSearchPath "lib/girepository-1.0" [
        pkgs.gdk-pixbuf
        pkgs.glib.out
        pkgs.graphene
        pkgs.gtk4
        pkgs.harfbuzz
        pkgs.gobject-introspection
        pkgs.pango.out
      ];
      nsoDataDirs = pkgs.lib.makeSearchPath "share" [
        pkgs.gsettings-desktop-schemas
        pkgs.gtk4
      ];
    in
    {
      packages.${system}.default = pkgs.rustPlatform.buildRustPackage rec {
        pname = "driftwm";
        version = (builtins.fromTOML (builtins.readFile ./Cargo.toml)).package.version;

        src = pkgs.lib.cleanSourceWith {
          src = ./.;
          filter = path: type:
            let baseName = builtins.baseNameOf path;
            in baseName != "target" && baseName != ".git" && baseName != ".direnv";
        };

        cargoLock = {
          lockFile = ./Cargo.lock;
          allowBuiltinFetchGit = true;
        };

        inherit nativeBuildInputs buildInputs;

        # Make sure the binary can find shared libraries at runtime
        postFixup = ''
          patchelf --add-rpath "${pkgs.lib.makeLibraryPath runtimeLibs}" $out/bin/driftwm
        '';

        postInstall = ''
          install -Dm755 resources/driftwm-session $out/bin/driftwm-session
          install -Dm644 resources/driftwm.desktop $out/share/wayland-sessions/driftwm.desktop
          install -Dm644 resources/driftwm-portals.conf $out/share/xdg-desktop-portal/driftwm-portals.conf
          install -Dm644 resources/driftwm.service $out/lib/systemd/user/driftwm.service
          install -Dm644 resources/driftwm-shutdown.target $out/lib/systemd/user/driftwm-shutdown.target
          install -Dm644 config.reference.toml $out/etc/driftwm/config.reference.toml
          mkdir -p $out/share/driftwm/nso
          cp -R extras/nso/assets extras/nso/config extras/nso/scripts extras/nso/widgets $out/share/driftwm/nso/
          for f in extras/wallpapers/*.glsl extras/wallpapers/*/*.glsl; do
            [ -e "$f" ] || continue
            rel="''${f#extras/wallpapers/}"
            install -Dm644 "$f" "$out/share/driftwm/wallpapers/$rel"
          done

        substituteInPlace $out/share/wayland-sessions/driftwm.desktop --replace-fail "Exec=driftwm-session" "Exec=$out/bin/driftwm-session"

        substituteInPlace $out/lib/systemd/user/driftwm.service --replace-fail "ExecStart=driftwm" "ExecStart=$out/bin/driftwm"

        makeWrapper ${nsoPython}/bin/python $out/bin/driftwm-nso-widget \
          --set NSO_DRIFTWM_ROOT "$out/share/driftwm/nso" \
          --prefix GI_TYPELIB_PATH : "${nsoTypelibPath}" \
          --prefix XDG_DATA_DIRS : "${nsoDataDirs}" \
          --prefix PATH : "${nsoRuntimePath}" \
          --add-flags "$out/share/driftwm/nso/scripts/nso_widget.py"

        makeWrapper ${pkgs.bash}/bin/bash $out/bin/driftwm-nso \
          --set NSO_DRIFTWM_ROOT "$out/share/driftwm/nso" \
          --set NSO_WIDGET_BIN "$out/bin/driftwm-nso-widget" \
          --prefix PATH : "$out/bin:${nsoRuntimePath}" \
          --add-flags "$out/share/driftwm/nso/scripts/launch.sh"
        '';

        passthru.providedSessions = [ "driftwm" ];

        meta = with pkgs.lib; {
          description = "A trackpad-first infinite canvas Wayland compositor";
          license = licenses.gpl3Plus;
          platforms = [ "x86_64-linux" ];
          mainProgram = "driftwm";
        };
      };

      apps.${system} = {
        nso = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/driftwm-nso";
        };

        nso-widget = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/driftwm-nso-widget";
        };
      };

      nixosModules.default =
        { config, lib, pkgs, ... }:
        let
          cfg = config.programs.driftwm;
          driftwmPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          options.programs.driftwm = {
            package = lib.mkOption {
              type = lib.types.package;
              default = driftwmPackage;
              description = "driftwm package to install.";
            };

            nso.enable = lib.mkEnableOption "the bundled Needy Streamer Overload driftwm widget suite";
          };

          config = lib.mkIf cfg.nso.enable {
            environment.systemPackages = [ cfg.package ];
          };
        };

      devShells.${system}.default = pkgs.mkShell {
        inherit nativeBuildInputs buildInputs;

        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath runtimeLibs;

        packages = [
          nsoPython
          pkgs.glib
          pkgs.gtk4
          pkgs.gobject-introspection
          pkgs.playerctl
          pkgs.xdg-utils
        ];

        GI_TYPELIB_PATH = nsoTypelibPath;
        XDG_DATA_DIRS = nsoDataDirs;
      };
    };
}
