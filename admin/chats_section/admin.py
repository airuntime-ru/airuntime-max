from django.contrib import admin

from chats_section.models import ChatSectionChat, ChatSectionFile, ChatSectionMessage


@admin.register(ChatSectionChat)
class ChatSectionChatAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "created_at")
    search_fields = ("title",)


@admin.register(ChatSectionMessage)
class ChatSectionMessageAdmin(admin.ModelAdmin):
    list_display = ("chat", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("content_markdown",)


@admin.register(ChatSectionFile)
class ChatSectionFileAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "project", "content_type", "size_bytes", "created_at")
    search_fields = ("original_filename", "object_key")
