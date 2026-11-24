jm runs on tomlkit 0.15.3. That release removed a private method jm used to place a new manifest key, so every command that added one failed with `AttributeError`.
