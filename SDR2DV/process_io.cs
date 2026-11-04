// Small .NET Framework 4.x compatible primitives used by the PowerShell core.
// No media or colour processing lives here.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;

namespace Sdr2dv {
    public sealed class LogPump {
        public const int TailLimit = 65536;
        public const long DiskLimit = 16 * 1024 * 1024;
        private readonly StringBuilder tail = new StringBuilder();
        public Exception Failure;
        public long Characters;
        public Task Worker;
        public string Tail { get { lock(tail) return tail.ToString(); } }
        public LogPump(StreamReader source, string path) : this(source, path, TailLimit) { }
        public LogPump(StreamReader source, string path, int tailLimit) {
            if (tailLimit < 1 || tailLimit > 4194304) throw new ArgumentOutOfRangeException("tailLimit");
            Worker = Task.Run(() => {
                StreamWriter disk = null;
                try {
                    if (!String.IsNullOrEmpty(path))
                        disk = new StreamWriter(new FileStream(path, FileMode.CreateNew, FileAccess.Write,
                            FileShare.ReadWrite | FileShare.Delete), new UTF8Encoding(false));
                    char[] buffer = new char[8192];
                    int read;
                    while ((read = source.Read(buffer, 0, buffer.Length)) != 0) {
                        lock(tail) {
                            tail.Append(buffer, 0, read);
                            if (tail.Length > tailLimit) tail.Remove(0, tail.Length - tailLimit);
                        }
                        if (disk != null && Characters < DiskLimit) {
                            try { disk.Write(buffer, 0, (int)Math.Min(read, DiskLimit - Characters)); disk.Flush(); }
                            catch (Exception e) { Failure = e; disk.Dispose(); disk = null; }
                        }
                        Characters += read;
                    }
                } catch (Exception e) { Failure = e; }
                finally { if (disk != null) disk.Dispose(); }
            });
        }
    }

    public static class Native {
        public static void ReplaceJournal(string source, string destination) {
            // PowerShell converts $null to an empty string for File.Replace's
            // backup parameter on some runtimes. Pass a real managed null.
            File.Replace(source, destination, null);
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool GetDiskFreeSpaceEx(string path, out ulong available, out ulong total, out ulong free);
        public static long AvailableBytes(string path) {
            ulong available, total, free;
            if (!GetDiskFreeSpaceEx(path, out available, out total, out free))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot query free space: " + path);
            return (long)Math.Min(available, (ulong)Int64.MaxValue);
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] internal static extern bool CloseHandle(IntPtr handle);
        [StructLayout(LayoutKind.Sequential)] struct Limits {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorking, MaxWorking;
            public uint ActiveProcesses;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters {
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] struct Extended {
            public Limits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        public static IntPtr AttachJob(Process child) {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception();
            Extended info = new Extended(); info.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE
            int size = Marshal.SizeOf(info);
            IntPtr buffer = Marshal.AllocHGlobal(size);
            try {
                Marshal.StructureToPtr(info, buffer, false);
                if (!SetInformationJobObject(job, 9, buffer, (uint)size)) throw new Win32Exception();
                if (!AssignProcessToJobObject(job, child.Handle) && !child.HasExited) throw new Win32Exception();
                return job;
            } catch { CloseHandle(job); throw; }
            finally { Marshal.FreeHGlobal(buffer); }
        }
        public static void CloseJob(IntPtr job) { if (job != IntPtr.Zero) CloseHandle(job); }
    }
}
