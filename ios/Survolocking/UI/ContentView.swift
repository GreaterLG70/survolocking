import SwiftUI

struct ContentView: View {

    @EnvironmentObject private var vm: AppViewModel

    @State private var phone = ""
    @State private var code = ""
    @State private var showFamily = false
    @State private var isSending = false

    var body: some View {
        NavigationStack {
            List {
                blockingSection
                contactsSection
                loginSection
                familySection
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Survolocking")
            .sheet(isPresented: $showFamily) {
                FamilyView()
            }
        }
    }

    // MARK: - 拦截状态

    private var blockingSection: some View {
        Section {
            HStack {
                Image(systemName: vm.blockingEnabled ? "checkmark.shield.fill" : "shield.slash")
                    .foregroundStyle(vm.blockingEnabled ? .green : .orange)
                Text(vm.blockingEnabled ? "拦截已启用" : "拦截未启用")
                Spacer()
            }

            if !vm.blockingEnabled {
                Text("请前往「系统设置 → 电话 → 来电阻止与身份识别」启用本应用")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Button("刷新拦截名单") {
                vm.reloadExtension()
            }

            if !vm.statusMessage.isEmpty {
                Text(vm.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("来电拦截")
        } footer: {
            Text("iOS 不支持静默挂断。被标记的号码由系统直接拦截，其余号码仅展示标签，需手动拒接。")
        }
    }

    // MARK: - 通讯录白名单

    private var contactsSection: some View {
        Section {
            HStack {
                Image(systemName: vm.contactsAuthorized ? "person.crop.circle.badge.checkmark" : "person.crop.circle.badge.exclamationmark")
                    .foregroundStyle(vm.contactsAuthorized ? .green : .orange)
                Text(vm.contactsAuthorized ? "通讯录白名单已就绪" : "未授权通讯录")
            }
            if !vm.contactsAuthorized {
                Button("授予通讯录权限") {
                    Task { await vm.requestContacts() }
                }
            }
        } header: {
            Text("白名单")
        } footer: {
            Text("通讯录联系人拥有最高优先级，永不被拦截。宁可漏接，不可误拦。")
        }
    }

    // MARK: - 登录

    private var loginSection: some View {
        Section {
            if vm.isLoggedIn {
                Text("已登录，云端协同已启用")
                    .foregroundStyle(.green)
            } else {
                TextField("手机号", text: $phone)
                    .keyboardType(.phonePad)
                HStack {
                    TextField("验证码", text: $code)
                        .keyboardType(.numberPad)
                    Button("获取验证码") {
                        Task {
                            isSending = true
                            if let devCode = try? await APIClient.shared.sendCode(phone: phone) {
                                // DEV 模式：验证码直接回显
                                code = devCode
                            }
                            isSending = false
                        }
                    }
                    .disabled(isSending || phone.count < 6)
                }
                Button("登录") {
                    Task {
                        try? await APIClient.shared.login(phone: phone, code: code)
                        await vm.refreshStatus()
                    }
                }
                .disabled(phone.count < 6 || code.count < 4)
            }
        } header: {
            Text("云端协同")
        } footer: {
            Text("不登录也可使用本地拦截。登录后启用家庭共享规则与日志分析。")
        }
    }

    // MARK: - 家庭组

    private var familySection: some View {
        Section {
            Button("管理家庭组") {
                showFamily = true
            }
            .disabled(!vm.isLoggedIn)
        } header: {
            Text("家庭共享")
        } footer: {
            Text("数据共享仅限家庭成员之间，退出后即停止共享。")
        }
    }
}

#Preview {
    ContentView().environmentObject(AppViewModel())
}
