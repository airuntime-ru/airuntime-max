from core.models import Chat, ChatFile, Message


class ChatSectionChat(Chat):
    class Meta:
        proxy = True
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"


class ChatSectionFile(ChatFile):
    class Meta:
        proxy = True
        verbose_name = "Файл чата"
        verbose_name_plural = "Файлы чата"


class ChatSectionMessage(Message):
    class Meta:
        proxy = True
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
