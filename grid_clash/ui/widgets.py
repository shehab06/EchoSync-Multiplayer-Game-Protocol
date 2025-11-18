"""
UI Widgets
"""
import pygame
import os
import sys

# Add the parent directory to the path so we can import from config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import *
from ui.colors import Colors

class Button:
    def __init__(self, rect, text, onclick=None, enabled=True):
        self.rect = rect
        self.text = text
        self.onclick = onclick
        self.enabled = enabled
        self.is_hover = False
        self.anim = 0.0
        self._last_mouse_down = False

    def update(self, mouse_pos, mouse_down):
        if not self.enabled:
            self.is_hover = False
            return

        self.is_hover = self.rect.collidepoint(mouse_pos)
        target = 1.0 if self.is_hover else 0.0
        self.anim += (target - self.anim) * 0.25

        if self.is_hover and mouse_down and not self._last_mouse_down:
            if self.onclick:
                self.onclick()

        self._last_mouse_down = mouse_down

    def draw(self, surface):
        if not self.enabled:
            # Draw disabled button
            pygame.draw.rect(surface, (140, 140, 140), self.rect, border_radius=8)
            self._draw_text(surface, (200, 200, 200))
        else:
            # Draw enabled button with hover effect
            bg_color = self._interpolate_color(Colors.BUTTON_NORMAL, Colors.BUTTON_HOVER, self.anim)
            pygame.draw.rect(surface, bg_color, self.rect, border_radius=8)
            self._draw_text(surface, Colors.BUTTON_TEXT)

    def _draw_text(self, surface, color):
        font = pygame.font.SysFont(None, 24)
        text_surf = font.render(self.text, True, color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        surface.blit(text_surf, text_rect)

    def _interpolate_color(self, color1, color2, factor):
        return tuple(int(c1 + (c2 - c1) * factor) for c1, c2 in zip(color1, color2))

class TextInput:
    def __init__(self, rect, placeholder="", max_length=20):
        self.rect = rect
        self.text = ""
        self.placeholder = placeholder
        self.max_length = max_length
        self.active = False

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        
        if event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key == pygame.K_RETURN:
                self.active = False
            elif len(self.text) < self.max_length and event.unicode.isprintable():
                self.text += event.unicode

    def draw(self, surface):
        # Draw background
        pygame.draw.rect(surface, WHITE, self.rect, border_radius=6)
        pygame.draw.rect(surface, BLACK, self.rect, 2, border_radius=6)
        
        # Draw text or placeholder
        font = pygame.font.SysFont(None, 22)
        if self.text:
            text_surf = font.render(self.text, True, BLACK)
        else:
            text_surf = font.render(self.placeholder, True, (150, 150, 150))
        
        text_rect = text_surf.get_rect(midleft=(self.rect.x + 10, self.rect.centery))
        surface.blit(text_surf, text_rect)

def draw_text(surface, text, size, pos, color=BLACK, center=False):
    font = pygame.font.SysFont(None, size)
    text_surf = font.render(text, True, color)
    if center:
        text_rect = text_surf.get_rect(center=pos)
    else:
        text_rect = text_surf.get_rect(topleft=pos)
    surface.blit(text_surf, text_rect)
    return text_rect

def centered_rect(center_x, center_y, width, height):
    return pygame.Rect(center_x - width // 2, center_y - height // 2, width, height)