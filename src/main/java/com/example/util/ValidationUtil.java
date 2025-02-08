package com.example.util;

public class ValidationUtil {
    public boolean validateEmail(String email) {
        System.err.println("Validating email: " + email);
        return email != null && email.contains("@");
    }
} 