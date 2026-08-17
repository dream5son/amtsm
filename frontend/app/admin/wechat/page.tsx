"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  fetchWeChatStatus,
  sendWeChatTest,
  WeChatChannelStatus,
  WeChatSendResult,
} from "@/lib/api";

const TO_USER_STORAGE_KEY = "amtsm.admin.wechat.to_user";
const DEFAULT_CONTENT = "AMTSM 测试消息";

const STATUS_LABEL: Record<WeChatChannelStatus["status"], string> = {
  available: "可用",
  not_ready: "未就绪",
  failed: "失败",
};

function statusTone(status: WeChatChannelStatus["status"]): string {
  if (status === "available") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "failed") return "border-rose-200 bg-rose-50 text-rose-800";
  return "border-amber-200 bg-amber-50 text-amber-900";
}

export default function AdminWeChatPage() {
  const [channel, setChannel] = useState<WeChatChannelStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [statusLoading, setStatusLoading] = useState(true);

  const [toUser, setToUser] = useState("");
  const [content, setContent] = useState(DEFAULT_CONTENT);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [result, setResult] = useState<WeChatSendResult | null>(null);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(TO_USER_STORAGE_KEY);
      if (saved) setToUser(saved);
    } catch {
      // ignore storage errors
    }

    let cancelled = false;
    setStatusLoading(true);
    void fetchWeChatStatus()
      .then((data) => {
        if (!cancelled) {
          setChannel(data);
          setStatusError("");
        }
      })
      .catch(() => {
        if (!cancelled) setStatusError("无法读取微信通道状态");
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError("");
    setResult(null);

    const userId = toUser.trim();
    const text = content.trim();
    if (!userId) {
      setFormError("请录入企业微信 UserID");
      return;
    }
    if (!text) {
      setFormError("请录入消息内容");
      return;
    }

    setSubmitting(true);
    try {
      window.localStorage.setItem(TO_USER_STORAGE_KEY, userId);
      const data = await sendWeChatTest(userId, text);
      setResult(data);
      setChannel(data.channel);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="w-full px-4 py-8 md:px-6 md:py-10">
      <section className="mx-auto w-full max-w-xl rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
        <p className="mb-2 inline-flex rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600">
          Admin
        </p>
        <h1 className="text-lg font-semibold text-slate-900">微信消息测试</h1>
        <p className="mt-1 text-sm text-slate-600">
          收件人是企业微信 UserID（不是个人微信号）。发件人凭证仍使用服务端环境变量，Secret 不会出现在本页。
        </p>

        <div className={`mt-4 rounded-xl border px-3 py-2 text-sm ${channel ? statusTone(channel.status) : "border-slate-200 bg-slate-50 text-slate-700"}`}>
          {statusLoading ? (
            <span>正在读取通道状态...</span>
          ) : statusError ? (
            <span>{statusError}</span>
          ) : channel ? (
            <div className="space-y-1">
              <div>
                通道状态：{STATUS_LABEL[channel.status]}
                {channel.configured ? " · 已配置" : " · 未配置"}
              </div>
              {channel.agent_id ? <div>AgentId：{channel.agent_id}</div> : null}
              {channel.corp_id_masked ? <div>CorpId：{channel.corp_id_masked}</div> : null}
              {channel.to_user_masked ? <div>默认收件人：{channel.to_user_masked}</div> : null}
              {channel.missing_fields.length > 0 ? (
                <div>缺失配置：{channel.missing_fields.join(", ")}</div>
              ) : null}
              {channel.last_error ? <div>最近错误：{channel.last_error}</div> : null}
            </div>
          ) : (
            <span>暂无通道状态</span>
          )}
        </div>

        <form onSubmit={(e) => void onSubmit(e)} className="mt-5 space-y-3">
          <label className="block text-sm">
            <span className="text-slate-600">企业微信 UserID</span>
            <input
              type="text"
              value={toUser}
              onChange={(e) => setToUser(e.target.value)}
              placeholder="zhangsan"
              autoComplete="off"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 transition focus:ring"
            />
            <span className="mt-1 block text-xs text-slate-500">多人可用 | 分隔，例如 zhangsan|lisi</span>
          </label>

          <label className="block text-sm">
            <span className="text-slate-600">消息内容</span>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={4}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-sky-200 transition focus:ring"
            />
          </label>

          {formError ? <p className="text-sm text-rose-600">{formError}</p> : null}

          <div className="flex justify-end pt-1">
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
            >
              {submitting ? "发送中..." : "发送"}
            </button>
          </div>
        </form>

        {result ? (
          <div
            className={`mt-4 rounded-xl border px-3 py-2 text-sm ${
              result.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"
            }`}
          >
            <div>{result.ok ? "发送成功" : "发送失败"}</div>
            {result.message ? <div>{result.message}</div> : null}
            {result.errcode != null ? <div>errcode：{result.errcode}</div> : null}
            {result.errmsg ? <div>errmsg：{result.errmsg}</div> : null}
            {result.invalid_user ? <div>无效用户：{result.invalid_user}</div> : null}
          </div>
        ) : null}
      </section>
    </main>
  );
}
