import SwiftUI

struct PasswordChangeView: View {
    @EnvironmentObject private var store: RadarStore
    @Environment(\.dismiss) private var dismiss
    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmation = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("修改密码").font(.title2).fontWeight(.semibold)
            Text("修改后，其他设备上的 App 需要重新登录。服务器客户端继续运行。")
                .font(.callout).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 7) {
                Text("当前密码").font(.caption).foregroundStyle(.secondary)
                SecureField("输入当前密码", text: $currentPassword).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("新密码").font(.caption).foregroundStyle(.secondary)
                SecureField("10 至 128 个字符", text: $newPassword).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("确认新密码").font(.caption).foregroundStyle(.secondary)
                SecureField("再次输入新密码", text: $confirmation).textFieldStyle(.roundedBorder).onSubmit { submit() }
            }
            if let error { ErrorBanner(text: error) }
            HStack {
                if store.isChangingPassword { ProgressView().controlSize(.small); Text("正在更新…").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("取消") { dismiss() }.disabled(store.isChangingPassword)
                Button("更新密码") { submit() }.buttonStyle(.borderedProminent)
                    .disabled(store.isChangingPassword || currentPassword.isEmpty || newPassword.isEmpty || confirmation.isEmpty)
            }
        }.padding(28).frame(width: 450)
            .interactiveDismissDisabled(store.isChangingPassword)
    }

    private func submit() {
        guard !store.isChangingPassword else { return }
        error = nil
        guard (10...128).contains(newPassword.count) else { error = "新密码需要 10 至 128 个字符。"; return }
        guard newPassword == confirmation else { error = "两次输入的新密码不一致。"; return }
        guard newPassword != currentPassword else { error = "新密码不能与当前密码相同。"; return }
        Task {
            if await store.changePassword(currentPassword: currentPassword, newPassword: newPassword) {
                currentPassword = ""
                newPassword = ""
                confirmation = ""
                dismiss()
            } else { error = store.errorMessage ?? "未能更新密码，请重试。" }
        }
    }
}
