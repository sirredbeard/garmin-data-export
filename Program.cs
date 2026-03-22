using System.Diagnostics;

var scriptPath = Path.Combine(AppContext.BaseDirectory, "garmin_export.py");
if (!File.Exists(scriptPath))
    scriptPath = Path.Combine(Directory.GetCurrentDirectory(), "garmin_export.py");

var pythonCommand = await FindPythonAsync();
if (pythonCommand is null)
{
    Console.Error.WriteLine("Python is not installed.");
    if (!await TryInstallPythonAsync())
    {
        Console.Error.WriteLine("Could not install Python automatically. Please install Python 3 manually.");
        Environment.Exit(1);
    }
    pythonCommand = await FindPythonAsync();
    if (pythonCommand is null)
    {
        Console.Error.WriteLine("Python was installed but is not on PATH. Please restart your terminal or add Python to PATH.");
        Environment.Exit(1);
    }
}

var psi = new ProcessStartInfo
{
    FileName = pythonCommand,
    ArgumentList = { scriptPath },
    UseShellExecute = false
};

foreach (var arg in args)
    psi.ArgumentList.Add(arg);

using var process = Process.Start(psi);
if (process is null)
{
    Console.Error.WriteLine("Failed to start Python.");
    Environment.Exit(1);
}

await process.WaitForExitAsync();
Environment.Exit(process.ExitCode);

static async Task<string?> FindPythonAsync()
{
    // Try "python" then "python3" (some systems only have python3)
    foreach (var candidate in new[] { "python", "python3" })
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = candidate,
                ArgumentList = { "--version" },
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            using var proc = Process.Start(psi);
            if (proc is null) continue;
            await proc.WaitForExitAsync();
            if (proc.ExitCode == 0)
                return candidate;
        }
        catch
        {
            // Not found, try next
        }
    }

    // Check common Windows install locations in case PATH isn't set
    if (OperatingSystem.IsWindows())
    {
        var localApps = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var candidates = new List<string>();

        foreach (var baseDir in new[] { localApps, programFiles })
        {
            var pythonDir = Path.Combine(baseDir, "Programs", "Python");
            if (Directory.Exists(pythonDir))
            {
                foreach (var dir in Directory.GetDirectories(pythonDir).OrderDescending())
                {
                    var exe = Path.Combine(dir, "python.exe");
                    if (File.Exists(exe)) candidates.Add(exe);
                }
            }

            // winget installs under Program Files\Python3xx
            foreach (var dir in Directory.GetDirectories(baseDir, "Python3*").OrderDescending())
            {
                var exe = Path.Combine(dir, "python.exe");
                if (File.Exists(exe)) candidates.Add(exe);
            }
        }

        foreach (var exe in candidates)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = exe,
                    ArgumentList = { "--version" },
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };
                using var proc = Process.Start(psi);
                if (proc is null) continue;
                await proc.WaitForExitAsync();
                if (proc.ExitCode == 0)
                    return exe;
            }
            catch
            {
                // Skip broken installs
            }
        }
    }

    return null;
}

static async Task<bool> TryInstallPythonAsync()
{
    if (!OperatingSystem.IsWindows())
    {
        Console.Error.WriteLine("Automatic install is only supported on Windows via winget.");
        return false;
    }

    // Check if winget is available
    try
    {
        var check = new ProcessStartInfo
        {
            FileName = "winget",
            ArgumentList = { "--version" },
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        using var checkProc = Process.Start(check);
        if (checkProc is null) return false;
        await checkProc.WaitForExitAsync();
        if (checkProc.ExitCode != 0) return false;
    }
    catch
    {
        Console.Error.WriteLine("winget is not available. Please install Python 3 manually.");
        return false;
    }

    Console.Write("Would you like to install Python 3 via winget? [Y/n] ");
    var response = Console.ReadLine()?.Trim();
    if (response is not null && response.Length > 0
        && !response.Equals("y", StringComparison.OrdinalIgnoreCase)
        && !response.Equals("yes", StringComparison.OrdinalIgnoreCase))
    {
        return false;
    }

    Console.WriteLine("Installing Python via winget...");
    var psi = new ProcessStartInfo
    {
        FileName = "winget",
        ArgumentList = { "install", "Python.Python.3.13", "--accept-source-agreements", "--accept-package-agreements" },
        UseShellExecute = false
    };
    using var proc = Process.Start(psi);
    if (proc is null) return false;
    await proc.WaitForExitAsync();

    if (proc.ExitCode != 0)
    {
        Console.Error.WriteLine("winget install failed.");
        return false;
    }

    Console.WriteLine("Python installed successfully.");

    // Refresh PATH from the registry so we can find the newly installed Python
    // without requiring the user to restart their terminal
    RefreshPath();
    return true;
}

static void RefreshPath()
{
    if (!OperatingSystem.IsWindows()) return;

    var machinePath = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.Machine) ?? "";
    var userPath = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User) ?? "";
    var combined = $"{userPath};{machinePath}";
    Environment.SetEnvironmentVariable("PATH", combined);
}
