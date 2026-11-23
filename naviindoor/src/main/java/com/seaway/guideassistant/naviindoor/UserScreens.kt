package com.seaway.guideassistant.naviindoor

import androidx.appcompat.app.AlertDialog
import com.seaway.guideassistant.naviindoor.data.*
import java.util.UUID

internal fun NativeIndoorActivity.showUserManagement() {
    val user = profile ?: return showHome()
    require(user.admin)
    screen("用户与组织") { showHome() }
    work("读取有权管理的用户与组织…", {
        api.rows("profiles", mapOf("select" to "id,email,role,organization_id")) to api.rows("organizations", mapOf("select" to "id,name,description"))
    }) { (users, organizations) ->
        body.label("权限以服务器账号和数据访问规则为准。", 15f)
        users.forEach { account ->
            val id = account.string("id")
            val org = organizations.firstOrNull { it.string("id") == account.string("organization_id") }?.string("name") ?: "未分配组织"
            body.button(account.string("email", id) + " · " + account.string("role") + " · " + org) {
                AlertDialog.Builder(this).setTitle(account.string("email", id)).setItems(arrayOf("设为管理员", "设为普通用户", "分配组织")) { _, which ->
                    if (which < 2) {
                        val role = if (which == 0) "admin" else "user"
                        confirm("更改用户权限", "将该账号设置为" + if (role == "admin") "管理员？" else "普通用户？") {
                            work("更新用户权限…", { api.update("profiles", obj("role" to role), mapOf("id" to "eq." + id)) }) { showUserManagement() }
                        }
                    } else {
                        val names = organizations.map { it.string("name") } + "移出组织"
                        AlertDialog.Builder(this).setTitle("分配组织").setItems(names.toTypedArray()) { _, index ->
                            val orgId = organizations.getOrNull(index)?.string("id")
                            work("更新所属组织…", { api.update("profiles", obj("organization_id" to orgId), mapOf("id" to "eq." + id)) }) { showUserManagement() }
                        }.show()
                    }
                }.show()
            }
        }
        body.button("新建组织") {
            ask("组织名称") { name ->
                work("创建组织…", { api.write("organizations", obj("id" to UUID.randomUUID().toString(), "name" to name, "description" to "", "created_by_admin_id" to user.id)) }) { showUserManagement() }
            }
        }
    }
}
