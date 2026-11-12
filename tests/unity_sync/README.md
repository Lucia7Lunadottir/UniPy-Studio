# Unity integration regression checks

Run `dotnet run --project tests/unity_sync` from the repository root for the
refresh scheduling/state tests. The API double deliberately makes the VS base
generator throw, matching VS Editor 2.0.27. These tests do not replace Unity execution.

For a real integration check, create an isolated Unity project, install this
repository's `unity/com.unipy.ide/package.json` as a local package, and install
`com.unity.ide.visualstudio` 2.0.27. Add `Assets/Probe.cs` containing
`public class Probe : UnityEngine.MonoBehaviour {}`. Copy `UnityBatchProbe.cs`
to `Assets/Editor`, then run the project in batch mode with
`-executeMethod UnityBatchProbe.Run`.

The probe calls the real generator, validates solution/project XML and Unity
references, and checks unchanged/stale generation state. Success requires exit
code 0 and `probe-passed.txt` in the project root. Do not run this probe in a
production project: it intentionally overwrites the generation stamp.

Verified on Unity 6000.6.7f1 with VS Editor 2.0.27 on Linux.
