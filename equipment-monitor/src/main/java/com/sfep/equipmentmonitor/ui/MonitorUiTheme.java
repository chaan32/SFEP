package com.sfep.equipmentmonitor.ui;

import javax.swing.AbstractButton;
import javax.swing.BorderFactory;
import javax.swing.ButtonModel;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTabbedPane;
import javax.swing.JTable;
import javax.swing.SwingConstants;
import javax.swing.border.Border;
import javax.swing.plaf.basic.BasicTabbedPaneUI;
import javax.swing.plaf.basic.BasicButtonUI;
import javax.swing.table.DefaultTableCellRenderer;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Component;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.LayoutManager;
import java.awt.RenderingHints;

/** POSCO-coloured, low-noise Swing primitives used by the historical monitor. */
final class MonitorUiTheme {
    static final Color POSCO_BLUE = new Color(0x05, 0x50, 0x7D);
    static final Color POSCO_LIGHT_BLUE = new Color(0x00, 0xA5, 0xE5);
    static final Color PAGE = new Color(0xF4, 0xF7, 0xF9);
    static final Color SURFACE = Color.WHITE;
    static final Color SURFACE_MUTED = new Color(0xF8, 0xFA, 0xFB);
    static final Color LINE = new Color(0xE5, 0xEB, 0xEF);
    static final Color TEXT = new Color(0x19, 0x24, 0x2D);
    static final Color TEXT_MUTED = new Color(0x6B, 0x78, 0x83);
    static final Color DANGER = new Color(0xC9, 0x2A, 0x2A);
    static final Color DANGER_TINT = new Color(0xFF, 0xF0, 0xF2);
    static final Color CAUTION = new Color(0xA8, 0x60, 0x00);
    static final Color CAUTION_TINT = new Color(0xFF, 0xF7, 0xE6);
    static final Color SUCCESS = new Color(0x18, 0x79, 0x4E);
    static final Color SUCCESS_TINT = new Color(0xEA, 0xF8, 0xF0);
    static final Color UNKNOWN_TINT = new Color(0xF0, 0xF3, 0xF5);
    private static final String FONT_FAMILY = chooseFontFamily();

    private MonitorUiTheme() { }

    static JPanel card(LayoutManager layout) {
        return new RoundedPanel(layout, SURFACE, 20);
    }

    static JPanel brandCard(LayoutManager layout) {
        return new RoundedPanel(layout, POSCO_BLUE, 22);
    }

    static JLabel label(String text, float size, int style, Color colour) {
        JLabel label = new JLabel(text);
        label.setFont(preferredFont(style, size));
        label.setForeground(colour);
        return label;
    }

    static Font preferredFont(int style, float size) {
        return new Font(FONT_FAMILY, style, Math.round(size));
    }

    private static String chooseFontFamily() {
        String[] candidates = {"Pretendard", "Apple SD Gothic Neo", "Noto Sans CJK KR", "Dialog"};
        String[] installed = java.awt.GraphicsEnvironment
                .getLocalGraphicsEnvironment().getAvailableFontFamilyNames();
        for (String candidate : candidates) {
            for (String available : installed) {
                if (candidate.equalsIgnoreCase(available)) return available;
            }
        }
        return Font.SANS_SERIF;
    }

    static void primaryButton(AbstractButton button) {
        button.setBackground(POSCO_BLUE);
        button.setForeground(Color.WHITE);
        button.setFont(preferredFont(Font.BOLD, 14));
        roundedButtonSurface(button, POSCO_BLUE, POSCO_BLUE);
        button.setPreferredSize(new Dimension(Math.max(84, button.getPreferredSize().width + 22), 40));
    }

    static void secondaryButton(AbstractButton button) {
        button.setBackground(new Color(0xE8, 0xF4, 0xFA));
        button.setForeground(POSCO_BLUE);
        button.setFont(preferredFont(Font.BOLD, 14));
        roundedButtonSurface(button, button.getBackground(), new Color(0xD2, 0xE9, 0xF3));
        button.setPreferredSize(new Dimension(Math.max(84, button.getPreferredSize().width + 22), 40));
    }

    static void quietButton(AbstractButton button) {
        button.setBackground(SURFACE_MUTED);
        button.setForeground(TEXT);
        button.setFont(preferredFont(Font.PLAIN, 14));
        roundedButtonSurface(button, SURFACE_MUTED, LINE);
        button.setPreferredSize(new Dimension(Math.max(76, button.getPreferredSize().width + 20), 40));
    }

    private static void roundedButtonSurface(AbstractButton button, Color fill, Color line) {
        button.setFocusPainted(true);
        button.setOpaque(false);
        button.setContentAreaFilled(false);
        button.setBorderPainted(false);
        button.setRolloverEnabled(true);
        button.setBorder(BorderFactory.createEmptyBorder(8, 14, 8, 14));
        button.setUI(new RoundedButtonUi(fill, line));
    }

    static void combo(JComboBox<?> combo) {
        combo.setBackground(SURFACE);
        combo.setForeground(TEXT);
        combo.setFont(preferredFont(Font.PLAIN, 14));
        combo.setBorder(BorderFactory.createCompoundBorder(
                BorderFactory.createLineBorder(LINE, 1, true),
                BorderFactory.createEmptyBorder(4, 8, 4, 8)));
        combo.setPreferredSize(new Dimension(Math.max(100, combo.getPreferredSize().width), 40));
    }

    static void tabs(JTabbedPane tabs) {
        tabs.setFont(preferredFont(Font.BOLD, 14));
        tabs.setForeground(TEXT_MUTED);
        tabs.setBackground(PAGE);
        tabs.setBorder(BorderFactory.createEmptyBorder(8, 0, 0, 0));
        tabs.setUI(new QuietTabbedPaneUi());
    }

    static void table(JTable table) {
        table.setFont(preferredFont(Font.PLAIN, 13));
        table.setForeground(TEXT);
        table.setBackground(SURFACE);
        table.setSelectionBackground(new Color(0xE4, 0xF4, 0xFB));
        table.setSelectionForeground(TEXT);
        table.setRowHeight(40);
        table.setShowGrid(false);
        table.setIntercellSpacing(new Dimension(0, 0));
        table.setDefaultRenderer(Object.class, new QuietCellRenderer());
        table.getTableHeader().setFont(preferredFont(Font.BOLD, 13));
        table.getTableHeader().setForeground(TEXT_MUTED);
        table.getTableHeader().setBackground(SURFACE_MUTED);
        table.getTableHeader().setPreferredSize(new Dimension(0, 42));
        table.getTableHeader().setBorder(BorderFactory.createMatteBorder(0, 0, 1, 0, LINE));
    }

    static void scrollPane(JScrollPane scroll) {
        scroll.setBorder(BorderFactory.createLineBorder(LINE, 1, true));
        scroll.getViewport().setBackground(SURFACE);
    }

    static Border cardPadding(int top, int left, int bottom, int right) {
        return BorderFactory.createEmptyBorder(top, left, bottom, right);
    }

    private static final class RoundedPanel extends JPanel {
        private final Color fill;
        private final int arc;

        private RoundedPanel(LayoutManager layout, Color fill, int arc) {
            super(layout);
            this.fill = fill;
            this.arc = arc;
            setBackground(fill);
            setOpaque(false);
        }

        @Override
        protected void paintComponent(Graphics graphics) {
            Graphics2D canvas = (Graphics2D) graphics.create();
            canvas.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            canvas.setColor(fill);
            canvas.fillRoundRect(0, 0, getWidth(), getHeight(), arc, arc);
            canvas.dispose();
            super.paintComponent(graphics);
        }

        @Override
        protected void paintBorder(Graphics graphics) {
            Graphics2D canvas = (Graphics2D) graphics.create();
            canvas.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            canvas.setColor(fill.equals(POSCO_BLUE) ? POSCO_BLUE : LINE);
            canvas.drawRoundRect(0, 0, Math.max(0, getWidth() - 1), Math.max(0, getHeight() - 1), arc, arc);
            canvas.dispose();
        }
    }

    private static final class RoundedButtonUi extends BasicButtonUI {
        private final Color fill;
        private final Color line;

        private RoundedButtonUi(Color fill, Color line) {
            this.fill = fill;
            this.line = line;
        }

        @Override
        public void paint(Graphics graphics, JComponent component) {
            AbstractButton button = (AbstractButton) component;
            ButtonModel model = button.getModel();
            Graphics2D canvas = (Graphics2D) graphics.create();
            canvas.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            Color surface = model.isPressed()
                    ? blend(fill, Color.BLACK, 0.10f)
                    : model.isRollover() ? blend(fill, Color.WHITE, 0.08f) : fill;
            canvas.setColor(surface);
            canvas.fillRoundRect(0, 0, component.getWidth(), component.getHeight(), 14, 14);
            canvas.setColor(line);
            canvas.drawRoundRect(
                    0, 0, Math.max(0, component.getWidth() - 1),
                    Math.max(0, component.getHeight() - 1), 14, 14);
            canvas.dispose();
            super.paint(graphics, component);
            if (button.isFocusOwner()) {
                Graphics2D focus = (Graphics2D) graphics.create();
                focus.setRenderingHint(
                        RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
                focus.setColor(POSCO_LIGHT_BLUE);
                focus.setStroke(new BasicStroke(2.0f));
                focus.drawRoundRect(
                        2, 2, Math.max(0, component.getWidth() - 5),
                        Math.max(0, component.getHeight() - 5), 11, 11);
                focus.dispose();
            }
        }

        private static Color blend(Color first, Color second, float secondWeight) {
            float firstWeight = 1.0f - secondWeight;
            return new Color(
                    Math.round(first.getRed() * firstWeight + second.getRed() * secondWeight),
                    Math.round(first.getGreen() * firstWeight + second.getGreen() * secondWeight),
                    Math.round(first.getBlue() * firstWeight + second.getBlue() * secondWeight),
                    first.getAlpha());
        }
    }

    private static final class QuietCellRenderer extends DefaultTableCellRenderer {
        private QuietCellRenderer() {
            setVerticalAlignment(SwingConstants.CENTER);
        }

        @Override
        public Component getTableCellRendererComponent(
                JTable table,
                Object value,
                boolean selected,
                boolean focused,
                int row,
                int column) {
            JLabel cell = (JLabel) super.getTableCellRendererComponent(
                    table, value, selected, focused, row, column);
            String text = value == null ? "" : value.toString();
            cell.setText(text);
            cell.setToolTipText(null);
            if (text.startsWith("sha256:") && text.length() > 24) {
                cell.setText(text.substring(0, 15) + "…" + text.substring(text.length() - 6));
                cell.setToolTipText(text);
            }
            cell.setBorder(BorderFactory.createEmptyBorder(0, 12, 0, 12));
            cell.setFont(preferredFont(isStatus(text) ? Font.BOLD : Font.PLAIN, 13));
            if (selected) {
                cell.setBackground(table.getSelectionBackground());
                cell.setForeground(table.getSelectionForeground());
            } else if (isDanger(text)) {
                cell.setBackground(DANGER_TINT);
                cell.setForeground(DANGER);
            } else if (isCaution(text)) {
                cell.setBackground(CAUTION_TINT);
                cell.setForeground(CAUTION);
            } else if (isNormal(text)) {
                cell.setBackground(SUCCESS_TINT);
                cell.setForeground(SUCCESS);
            } else if (text.startsWith("? ")) {
                cell.setBackground(UNKNOWN_TINT);
                cell.setForeground(TEXT_MUTED);
            } else {
                cell.setBackground(row % 2 == 0 ? SURFACE : SURFACE_MUTED);
                cell.setForeground(TEXT);
            }
            return cell;
        }

        private static boolean isStatus(String value) {
            return isDanger(value) || isCaution(value) || isNormal(value) || value.startsWith("? ");
        }

        private static boolean isDanger(String value) {
            return value.startsWith("■ ") || value.startsWith("! ");
        }

        private static boolean isCaution(String value) {
            return value.startsWith("▲ ");
        }

        private static boolean isNormal(String value) {
            return value.startsWith("● ");
        }
    }

    private static final class QuietTabbedPaneUi extends BasicTabbedPaneUI {
        @Override
        protected void installDefaults() {
            super.installDefaults();
            tabAreaInsets = new java.awt.Insets(0, 4, 0, 4);
            tabInsets = new java.awt.Insets(11, 18, 11, 18);
            selectedTabPadInsets = new java.awt.Insets(0, 0, 0, 0);
            contentBorderInsets = new java.awt.Insets(8, 0, 0, 0);
        }

        @Override
        protected void paintTabBackground(
                Graphics graphics, int placement, int index,
                int x, int y, int width, int height, boolean selected) {
            Graphics2D canvas = (Graphics2D) graphics.create();
            canvas.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            canvas.setColor(selected ? POSCO_BLUE : SURFACE);
            canvas.fillRoundRect(x + 2, y + 2, width - 4, height - 4, 14, 14);
            canvas.dispose();
        }

        @Override
        protected void paintTabBorder(
                Graphics graphics, int placement, int index,
                int x, int y, int width, int height, boolean selected) { }

        @Override
        protected void paintContentBorder(Graphics graphics, int placement, int selectedIndex) { }

        @Override
        protected void paintText(
                Graphics graphics, int placement, Font font,
                java.awt.FontMetrics metrics, int index, String title,
                java.awt.Rectangle rectangle, boolean selected) {
            graphics.setFont(font);
            graphics.setColor(selected ? Color.WHITE : TEXT_MUTED);
            int mnemonic = tabPane.getDisplayedMnemonicIndexAt(index);
            javax.swing.plaf.basic.BasicGraphicsUtils.drawStringUnderlineCharAt(
                    graphics, title, mnemonic, rectangle.x,
                    rectangle.y + metrics.getAscent());
        }
    }
}
