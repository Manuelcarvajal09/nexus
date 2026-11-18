-- Nexus managed fragment. Loading is deferred to the next session.
hl.bind("SUPER + CTRL + SHIFT + V", hl.dsp.exec_cmd("/home/manuel/.local/bin/nexus panel"))
hl.bind("SUPER + CTRL + SHIFT + C", hl.dsp.exec_cmd("/home/manuel/.local/bin/nexus stack"))
hl.on("hyprland.start", function()
    hl.exec_cmd("/home/manuel/.local/bin/nexus session-start")
end)
