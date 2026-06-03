<script setup>
import { FrappeUIProvider, Dialogs } from 'frappe-ui'
</script>

<template>
  <FrappeUIProvider>
    <router-view />
    <!-- Host for confirmDialog()'s ConfirmDialog instances. confirmDialog()
         pushes onto frappe-ui's `dialogs` array, which only <Dialogs /> renders;
         without this mount the confirm never appears and its onConfirm never
         fires, so every confirm-based action (Terminate, Rebuild, Delete) is a
         silent no-op. FrappeUIProvider gives us ToastProvider but not this. -->
    <Dialogs />
  </FrappeUIProvider>
</template>
