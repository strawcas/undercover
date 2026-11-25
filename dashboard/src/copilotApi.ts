import { post, request } from "./api";
import type { CopilotConversation, CopilotDetail } from "./copilotTypes";

const conversationsPath = "/api/copilot/conversations";
const conversationPath = (id: string) =>
  `${conversationsPath}/${encodeURIComponent(id)}`;
const bearerHeader = (token: string) => ({ Authorization: `Bearer ${token}` });

export const copilotApi = {
  listCopilotConversations: (signal?: AbortSignal) =>
    request<CopilotConversation[]>(conversationsPath, { signal }),
  createCopilotConversation: (token: string) =>
    request<CopilotConversation>(conversationsPath, {
      method: "POST",
      headers: bearerHeader(token),
    }),
  getCopilotConversation: (id: string, signal: AbortSignal) =>
    request<CopilotDetail>(conversationPath(id), { signal }),
  sendCopilotMessage: (id: string, message: string, token: string) =>
    post<{ turn_id: string }>(
      `${conversationPath(id)}/messages`,
      { message },
      bearerHeader(token),
    ),
  answerCopilotConfirmation: (
    id: string,
    turnId: string,
    confirmed: boolean,
    token: string,
  ) =>
    post<{ turn_id: string }>(
      `${conversationPath(id)}/confirmation`,
      {
        turn_id: turnId,
        confirmed,
      },
      bearerHeader(token),
    ),
};
