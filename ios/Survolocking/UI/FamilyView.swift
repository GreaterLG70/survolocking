import SwiftUI

struct FamilyView: View {

    @State private var familyName = ""
    @State private var invitePhone = ""
    @State private var members: [MemberRow] = []
    @State private var familyId: Int?
    @State private var message = ""
    @Environment(\.dismiss) private var dismiss

    struct MemberRow: Identifiable {
        let id: Int
        let name: String
        let isCreator: Bool
    }

    var body: some View {
        NavigationStack {
            Form {
                if familyId == nil {
                    createSection
                } else {
                    memberSection
                    inviteSection
                }

                if !message.isEmpty {
                    Section {
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("家庭组")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .task { await loadMembers() }
        }
    }

    private var createSection: some View {
        Section {
            TextField("家庭组名称", text: $familyName)
            Button("创建家庭组") {
                Task { await createFamily() }
            }
            .disabled(familyName.trimmingCharacters(in: .whitespaces).isEmpty)
        } header: {
            Text("创建")
        } footer: {
            Text("每个用户只能加入一个家庭组。")
        }
    }

    private var memberSection: some View {
        Section {
            ForEach(members) { m in
                HStack {
                    Text(m.name)
                    Spacer()
                    if m.isCreator {
                        Text("创建者")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Button("刷新") { Task { await loadMembers() } }
            Button("退出家庭组", role: .destructive) {
                Task { await leaveFamily() }
            }
        } header: {
            Text("成员")
        }
    }

    private var inviteSection: some View {
        Section {
            TextField("成员手机号", text: $invitePhone)
                .keyboardType(.phonePad)
            Button("发送邀请") { Task { await invite() } }
                .disabled(invitePhone.count < 6)
        } header: {
            Text("邀请成员")
        } footer: {
            Text("邀请 48 小时内有效。对方接受后即开始共享拦截规则。")
        }
    }

    // MARK: - 网络操作

    private func createFamily() async {
        do {
            let data = try await APIClient.shared.createFamily(name: familyName)
            if let id = data["family_id"] as? Int {
                familyId = id
                message = "家庭组创建成功"
                await loadMembers()
            }
        } catch {
            message = error.localizedDescription
        }
    }

    private func invite() async {
        guard let familyId else { return }
        do {
            _ = try await APIClient.shared.inviteMember(familyId: familyId, phone: invitePhone)
            message = "邀请已发送"
            invitePhone = ""
        } catch {
            message = error.localizedDescription
        }
    }

    private func loadMembers() async {
        guard let data = try? await APIClient.shared.familyMembers(),
              let fid = data["family_id"] as? Int, fid > 0 else {
            familyId = nil
            return
        }
        familyId = fid

        guard let arr = data["members"] as? [[String: Any]] else { return }
        members = arr.compactMap { o in
            guard let id = o["user_id"] as? Int else { return nil }
            let nickname = o["nickname"] as? String
            let masked = o["phone_masked"] as? String ?? ""
            let name = (nickname?.isEmpty == false) ? nickname! : masked
            return MemberRow(id: id, name: name, isCreator: o["is_creator"] as? Bool ?? false)
        }
    }

    private func leaveFamily() async {
        do {
            try await APIClient.shared.leaveFamily()
            familyId = nil
            members = []
            message = "已退出家庭组"
        } catch {
            message = error.localizedDescription
        }
    }
}

#Preview {
    FamilyView()
}
