package com.sfep.equipmentmonitor.ui;

import org.junit.jupiter.api.Test;

import javax.swing.JLabel;
import javax.swing.JButton;
import javax.swing.JTable;
import javax.swing.SwingUtilities;
import javax.swing.table.DefaultTableModel;
import java.awt.Component;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class MonitorUiThemeTest {
    @Test
    void paintsThePrimaryActionAsARoundedPoscoBlueSurface() throws Exception {
        BufferedImage image = onEdt(() -> {
            JButton button = new JButton("시작");
            MonitorUiTheme.primaryButton(button);
            button.setSize(100, 40);
            BufferedImage rendered = new BufferedImage(100, 40, BufferedImage.TYPE_INT_ARGB);
            Graphics2D graphics = rendered.createGraphics();
            button.paint(graphics);
            graphics.dispose();
            return rendered;
        });

        assertThat(new Color(image.getRGB(10, 20), true)).isEqualTo(Color.decode("#05507D"));
        assertThat(new Color(image.getRGB(0, 0), true).getAlpha()).isZero();
    }

    @Test
    void paintsAVisibleAccentRingForTheKeyboardFocusedButton() throws Exception {
        FocusedButtonRender render = onEdt(() -> {
            JButton button = new JButton("시작") {
                @Override
                public boolean isFocusOwner() {
                    return true;
                }
            };
            MonitorUiTheme.primaryButton(button);
            button.setSize(100, 40);
            BufferedImage image = new BufferedImage(100, 40, BufferedImage.TYPE_INT_ARGB);
            Graphics2D graphics = image.createGraphics();
            button.paint(graphics);
            graphics.dispose();
            return new FocusedButtonRender(button.isFocusPainted(), image);
        });

        assertThat(render.focusPainted()).isTrue();
        assertThat(new Color(render.image().getRGB(50, 2), true))
                .isEqualTo(MonitorUiTheme.POSCO_LIGHT_BLUE);
    }

    @Test
    void abbreviatesLongHashIdentifiersInAnyTableWithoutChangingTheTableValue() throws Exception {
        String identifier = "sha256:bd8bec83ca6f3ff9937b526b4aec3e985ef212b8bccc0bb96efd90acedf41086";
        JTable table = onEdt(() -> {
            DefaultTableModel model = new DefaultTableModel(new Object[]{"봉인된 값"}, 0);
            model.addRow(new Object[]{identifier});
            JTable created = new JTable(model);
            MonitorUiTheme.table(created);
            return created;
        });

        JLabel rendered = onEdt(() -> (JLabel) table.prepareRenderer(
                table.getCellRenderer(0, 0), 0, 0));
        String renderedText = onEdt(() -> rendered.getText());
        String tooltip = onEdt(() -> rendered.getToolTipText());
        Object modelValue = onEdt(() -> table.getValueAt(0, 0));

        assertThat(renderedText).isEqualTo("sha256:bd8bec83…f41086");
        assertThat(tooltip).isEqualTo(identifier);
        assertThat(modelValue).isEqualTo(identifier);
    }

    private static <T> T onEdt(ThrowingSupplier<T> action) throws Exception {
        if (SwingUtilities.isEventDispatchThread()) return action.get();
        AtomicReference<T> value = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        SwingUtilities.invokeAndWait(() -> {
            try {
                value.set(action.get());
            } catch (Throwable error) {
                failure.set(error);
            }
        });
        if (failure.get() instanceof Exception exception) throw exception;
        if (failure.get() != null) throw new AssertionError(failure.get());
        return value.get();
    }

    @FunctionalInterface
    private interface ThrowingSupplier<T> {
        T get() throws Exception;
    }

    private record FocusedButtonRender(boolean focusPainted, BufferedImage image) { }
}
