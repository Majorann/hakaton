import { apiFetch } from "./api.js";
import { byId } from "./display.js";
import { renderReminder } from "./queue-view.js";

export function bindSettingsDialog(refresh) {
  let loadedSettings;
  let settingsRequest = 0;
  let savingSettings = false;
  const settingsDialog = byId("settings-dialog");
  const followupSetting = byId("settings-followup");
  const intervalSetting = byId("settings-followup-interval");
  const archiveAgentSetting = byId("settings-archive-agent");
  const archiveModelSelect = byId("settings-archive-model");
  const modelSelect = byId("settings-model");
  let settingsModels = [];

  function showFollowupSettings() {
    intervalSetting.hidden = archiveAgentSetting.hidden = !followupSetting.checked;
  }

  function updateArchiveModels(selected) {
    const available = [...new Set([...(settingsModels ?? []), selected])].filter(Boolean);
    archiveModelSelect.replaceChildren(new Option("Нет", ""));
    for (const name of available) {
      archiveModelSelect.add(new Option(name === modelSelect.value ? `${name} (основная)` : name, name));
    }
    archiveModelSelect.value = available.includes(selected) ? selected : "";
    const availability = settingsModels === null
      ? "Ollama недоступна. Список локальных моделей загрузится, когда Ollama снова заработает."
      : available.length
        ? ""
        : "Ollama не сообщила ни об одной установленной локальной модели.";
    byId("settings-archive-note").textContent = [
      "С выбранной моделью архив и основная очередь запускаются параллельно. При выборе «Нет» они работают по очереди.",
      availability,
    ].filter(Boolean).join(" ");
  }

  followupSetting.addEventListener("change", showFollowupSettings);
  modelSelect.addEventListener("change", () => updateArchiveModels(archiveModelSelect.value));

  function settingsMessage(message, error = false) {
    const note = byId("settings-message");
    note.textContent = message;
    note.dataset.error = String(error);
    note.hidden = !message;
  }

  byId("open-settings").addEventListener("click", async () => {
    const request = ++settingsRequest;
    loadedSettings = null;
    byId("settings-config").disabled = true;
    byId("save-settings").disabled = true;
    settingsMessage("Загружаем настройки…");
    settingsDialog.showModal();
    try {
      const response = await apiFetch("/api/settings");
      const data = await response.json();
      if (request !== settingsRequest || !settingsDialog.open) return;
      if (!response.ok) throw new Error(data.error);
      loadedSettings = {
        max_vacancies: data.max_vacancies,
        model: data.model,
        archive_model: data.archive_model,
        reminder_hours: data.reminder_hours,
        followup_enabled: data.followup_enabled,
        followup_minutes: data.followup_minutes,
      };
      byId("settings-limit").max = data.max_allowed;
      byId("settings-limit").value = data.max_vacancies;
      byId("settings-reminder").value = data.reminder_hours;
      followupSetting.checked = data.followup_enabled;
      showFollowupSettings();
      byId("settings-followup-minutes").value = data.followup_minutes;
      settingsModels = data.models;
      modelSelect.replaceChildren();
      for (const name of new Set([data.model, ...(data.models ?? [])])) {
        modelSelect.add(new Option(name, name));
      }
      modelSelect.value = data.model;
      modelSelect.disabled = !data.models?.length;
      updateArchiveModels(data.archive_model);
      byId("settings-model-note").textContent = data.models === null
        ? "Ollama недоступна. Можно сохранить остальные настройки."
        : "Применяется со следующего запуска";
      byId("settings-config").disabled = false;
      byId("save-settings").disabled = false;
      settingsMessage("");
    } catch (error) {
      if (request === settingsRequest && settingsDialog.open) {
        settingsMessage(error.message || "Не удалось загрузить настройки.", true);
      }
    }
  });
  byId("cancel-settings").addEventListener("click", () => settingsDialog.close());
  settingsDialog.addEventListener("cancel", (event) => {
    if (savingSettings) event.preventDefault();
  });
  settingsDialog.addEventListener("close", () => {
    settingsRequest++;
  });
  byId("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!loadedSettings || savingSettings) return;
    const values = {
      max_vacancies: byId("settings-limit").valueAsNumber,
      model: byId("settings-model").value,
      archive_model: archiveModelSelect.value,
      reminder_hours: Number(byId("settings-reminder").value),
      followup_enabled: followupSetting.checked,
      followup_minutes: Number(byId("settings-followup-minutes").value),
    };
    savingSettings = true;
    byId("save-settings").disabled = true;
    byId("cancel-settings").disabled = true;
    settingsMessage("");
    try {
      if (JSON.stringify(values) !== JSON.stringify(loadedSettings)) {
        const response = await apiFetch("/api/settings", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        await refresh();
      }
      settingsDialog.close();
      renderReminder();
    } catch (error) {
      settingsMessage(error.message || "Не удалось сохранить настройки.", true);
    } finally {
      savingSettings = false;
      byId("save-settings").disabled = false;
      byId("cancel-settings").disabled = false;
    }
  });
}
